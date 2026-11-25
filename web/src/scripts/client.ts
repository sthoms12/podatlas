import { navigate } from 'astro:transitions/client';
import Fuse from 'fuse.js';
import maplibregl from 'maplibre-gl';
import type {
  CategoryFilter,
  CitiesGeoJSON,
  CityData,
  DrawerTab,
  EpisodeRecord,
  SearchIndexItem,
} from '../types/podatlas';
import { CATEGORY_COLORS, formatTime } from '../lib/format';
import {
  buildCityOpml,
  buildTripItineraryText,
  triggerFileDownload,
} from '../lib/exportPack';

const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const SPEEDS = [1, 1.25, 1.5, 2];

interface ClientRuntime {
  initializedPersisted: boolean;
  map: maplibregl.Map | null;
  mapReady: boolean;
  geoJson: CitiesGeoJSON | null;
  activeCategory: CategoryFilter;
  pendingTab: DrawerTab | null;
  fuse: Fuse<SearchIndexItem> | null;
  searchLoaded: boolean;
  currentCity: CityData | null;
  // Audio player state
  currentEpisode: EpisodeRecord | null;
  isPlaying: boolean;
  isPreviewMode: boolean;
  currentSec: number;
  durationSec: number;
  speedIdx: number;
  previewTimer: number | null;
}

const runtime: ClientRuntime = {
  initializedPersisted: false,
  map: null,
  mapReady: false,
  geoJson: null,
  activeCategory: 'All',
  pendingTab: null,
  fuse: null,
  searchLoaded: false,
  currentCity: null,
  currentEpisode: null,
  isPlaying: false,
  isPreviewMode: false,
  currentSec: 0,
  durationSec: 2880,
  speedIdx: 0,
  previewTimer: null,
};

function readCurrentCityPayload(): CityData | null {
  const el = document.getElementById('podatlas-city-payload');
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as CityData;
  } catch {
    return null;
  }
}

function getFilteredGeoJson(): CitiesGeoJSON {
  if (!runtime.geoJson) return { type: 'FeatureCollection', features: [] };
  if (runtime.activeCategory === 'All') return runtime.geoJson;
  const cat = runtime.activeCategory;
  return {
    type: 'FeatureCollection',
    features: runtime.geoJson.features.filter(
      (f) => f.properties.primaryCategory === cat || f.properties.categories.includes(cat)
    ),
  };
}

function syncMapFilterAndPills(): void {
  const filtered = getFilteredGeoJson();
  if (runtime.map && runtime.mapReady) {
    const source = runtime.map.getSource('podatlas-cities') as maplibregl.GeoJSONSource | undefined;
    if (source) {
      source.setData(filtered as unknown as GeoJSON.GeoJSON);
    }
  }

  const statsEl = document.getElementById('podatlas-stats-label');
  if (statsEl) {
    const features = filtered.features;
    const eps = features.reduce((s, f) => s + f.properties.episodeCount, 0);
    const books = features.reduce((s, f) => s + f.properties.bookCount, 0);
    const places = features.reduce((s, f) => s + f.properties.placeCount, 0);
    statsEl.textContent = `${features.length} Cities · ${eps} Episodes · ${books} Books · ${places} Places`;
  }

  const visibleIds = new Set(filtered.features.map((f) => f.properties.id));
  const selectedId = runtime.currentCity?.id ?? '';
  document.querySelectorAll<HTMLAnchorElement>('[data-city-pill]').forEach((pill) => {
    const id = pill.dataset.cityPill || '';
    const isVisible = visibleIds.size === 0 || visibleIds.has(id);
    pill.classList.toggle('hidden', !isVisible);
    const isActive = id === selectedId;
    pill.className = `city-pill shrink-0 px-2.5 py-1 rounded-full text-xs font-medium border transition ${
      isVisible ? '' : 'hidden '
    }${
      isActive
        ? 'bg-sky-500 text-slate-950 border-sky-400 shadow-sm'
        : 'bg-slate-900/90 backdrop-blur text-slate-200 border-slate-700/80 hover:bg-slate-800'
    }`;
  });
}

function focusSelectedCityOnMap(): void {
  if (!runtime.map || !runtime.mapReady || !runtime.currentCity) return;
  const cityId = runtime.currentCity.id;
  if (runtime.map.getLayer('city-selected-halo')) {
    runtime.map.setFilter('city-selected-halo', ['==', ['get', 'id'], cityId]);
  }
  runtime.map.flyTo({
    center: [runtime.currentCity.lon, runtime.currentCity.lat],
    zoom: Math.max(runtime.map.getZoom(), 3.6),
    duration: 850,
    essential: true,
  });
}

async function initMapOnce(): Promise<void> {
  const container = document.getElementById('podatlas-map-canvas');
  if (!container || runtime.map) return;

  try {
    const res = await fetch('/cities.geojson');
    if (res.ok) {
      runtime.geoJson = (await res.json()) as CitiesGeoJSON;
    }
  } catch (err) {
    console.error('Failed to load cities.geojson', err);
  }

  const map = new maplibregl.Map({
    container,
    style: OPENFREEMAP_STYLE,
    center: [12, 26],
    zoom: 1.75,
    minZoom: 1.1,
    maxZoom: 12,
    attributionControl: { compact: true },
  });

  const popup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    offset: 14,
  });

  runtime.map = map;

  map.on('load', () => {
    runtime.mapReady = true;
    const initialData = getFilteredGeoJson();

    map.addSource('podatlas-cities', {
      type: 'geojson',
      data: initialData as unknown as GeoJSON.GeoJSON,
      cluster: true,
      clusterMaxZoom: 3,
      clusterRadius: 36,
    });

    map.addLayer({
      id: 'city-clusters',
      type: 'circle',
      source: 'podatlas-cities',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#0ea5e9',
        'circle-radius': ['step', ['get', 'point_count'], 16, 5, 20, 15, 25],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0f172a',
        'circle-opacity': 0.9,
      },
    });

    map.addLayer({
      id: 'city-cluster-count',
      type: 'symbol',
      source: 'podatlas-cities',
      filter: ['has', 'point_count'],
      layout: { 'text-field': '{point_count_abbreviated}', 'text-size': 12 },
      paint: { 'text-color': '#020617' },
    });

    map.addLayer({
      id: 'city-selected-halo',
      type: 'circle',
      source: 'podatlas-cities',
      filter: ['==', ['get', 'id'], runtime.currentCity?.id ?? ''],
      paint: {
        'circle-radius': 18,
        'circle-color': '#38bdf8',
        'circle-opacity': 0.28,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#38bdf8',
      },
    });

    map.addLayer({
      id: 'city-points',
      type: 'circle',
      source: 'podatlas-cities',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': [
          'match',
          ['get', 'primaryCategory'],
          'Travel',
          CATEGORY_COLORS.Travel,
          'History',
          CATEGORY_COLORS.History,
          'Food',
          CATEGORY_COLORS.Food,
          'Culture',
          CATEGORY_COLORS.Culture,
          '#38bdf8',
        ],
        'circle-radius': ['+', 6, ['min', 8, ['*', ['get', 'episodeCount'], 1.4]]],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0f172a',
      },
    });

    map.addLayer({
      id: 'city-labels',
      type: 'symbol',
      source: 'podatlas-cities',
      filter: ['!', ['has', 'point_count']],
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 12,
        'text-offset': [0, 1.35],
        'text-anchor': 'top',
        'text-optional': true,
      },
      paint: {
        'text-color': '#0f172a',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.5,
      },
    });

    map.on('click', 'city-clusters', async (e) => {
      const features = map.queryRenderedFeatures(e.point, { layers: ['city-clusters'] });
      const clusterId = features[0]?.properties?.cluster_id;
      const source = map.getSource('podatlas-cities') as maplibregl.GeoJSONSource | undefined;
      if (!source || clusterId === undefined) return;
      const zoom = await source.getClusterExpansionZoom(clusterId);
      const geom = features[0].geometry as GeoJSON.Point;
      map.easeTo({ center: geom.coordinates as [number, number], zoom });
    });

    map.on('click', 'city-points', (e) => {
      const feature = e.features?.[0];
      const cityId = feature?.properties?.id;
      if (cityId) {
        navigate(`/city/${encodeURIComponent(String(cityId))}`);
      }
    });

    map.on('mouseenter', 'city-points', (e) => {
      map.getCanvas().style.cursor = 'pointer';
      const feature = e.features?.[0];
      if (!feature) return;
      const geom = feature.geometry as GeoJSON.Point;
      const props = feature.properties;
      popup
        .setLngLat(geom.coordinates as [number, number])
        .setHTML(
          `<div style="font-size:12px;line-height:1.4">
            <div style="font-weight:600;color:#f8fafc">${props.name}, ${props.country}</div>
            <div style="color:#94a3b8;margin-top:2px">${props.episodeCount} episodes · ${props.bookCount} books · ${props.placeCount} places</div>
            <div style="color:#38bdf8;margin-top:4px;font-weight:500">Click to open city drawer →</div>
          </div>`
        )
        .addTo(map);
    });

    map.on('mouseleave', 'city-points', () => {
      map.getCanvas().style.cursor = '';
      popup.remove();
    });

    map.on('mouseenter', 'city-clusters', () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', 'city-clusters', () => {
      map.getCanvas().style.cursor = '';
    });

    syncMapFilterAndPills();
    focusSelectedCityOnMap();
  });

  document.getElementById('podatlas-zoom-in')?.addEventListener('click', () => map.zoomIn());
  document.getElementById('podatlas-zoom-out')?.addEventListener('click', () => map.zoomOut());
  document
    .getElementById('podatlas-zoom-reset')
    ?.addEventListener('click', () => map.flyTo({ center: [12, 26], zoom: 1.75, duration: 750 }));
}

// --- Persistent Audio Player ---

function updatePlayerUI(): void {
  const playIcon = document.getElementById('podatlas-icon-play');
  const pauseIcon = document.getElementById('podatlas-icon-pause');
  playIcon?.classList.toggle('hidden', runtime.isPlaying);
  pauseIcon?.classList.toggle('hidden', !runtime.isPlaying);

  const curEl = document.getElementById('podatlas-player-current');
  const durEl = document.getElementById('podatlas-player-duration');
  const scrubber = document.getElementById('podatlas-player-scrubber') as HTMLInputElement | null;
  if (curEl) curEl.textContent = formatTime(runtime.currentSec);
  if (durEl) durEl.textContent = formatTime(runtime.durationSec);
  if (scrubber) {
    scrubber.max = String(runtime.durationSec || 100);
    scrubber.value = String(runtime.currentSec);
  }

  // Highlight active episode card in drawer
  document.querySelectorAll<HTMLElement>('[data-episode-card]').forEach((card) => {
    const active = card.dataset.episodeCard === runtime.currentEpisode?.id;
    card.classList.toggle('border-sky-500/80', active);
    card.classList.toggle('ring-1', active);
    card.classList.toggle('ring-sky-500/20', active);
    card.classList.toggle('border-slate-800', !active);
  });
}

function stopPreviewTimer(): void {
  if (runtime.previewTimer !== null) {
    window.clearInterval(runtime.previewTimer);
    runtime.previewTimer = null;
  }
}

function startPreviewTimer(): void {
  stopPreviewTimer();
  const speed = SPEEDS[runtime.speedIdx];
  runtime.previewTimer = window.setInterval(() => {
    if (!runtime.isPlaying || !runtime.isPreviewMode) return;
    if (runtime.currentSec + 1 >= runtime.durationSec) {
      runtime.currentSec = runtime.durationSec;
      runtime.isPlaying = false;
      stopPreviewTimer();
    } else {
      runtime.currentSec += 1;
    }
    updatePlayerUI();
  }, 1000 / speed);
}

function seekTo(sec: number): void {
  const clamped = Math.max(0, Math.min(runtime.durationSec, sec));
  runtime.currentSec = clamped;
  const audio = document.getElementById('podatlas-audio-el') as HTMLAudioElement | null;
  if (!runtime.isPreviewMode && audio) {
    audio.currentTime = clamped;
  }
  updatePlayerUI();
}

function playEpisode(episode: EpisodeRecord, city: CityData, startSec = 0, hintText = ''): void {
  runtime.currentEpisode = episode;
  runtime.currentSec = startSec;
  runtime.durationSec = episode.durationSec || 2400;

  const titleEl = document.getElementById('podatlas-player-title');
  const showEl = document.getElementById('podatlas-player-show');
  const hintEl = document.getElementById('podatlas-player-hint');
  const markersEl = document.getElementById('podatlas-mention-markers');

  if (titleEl) titleEl.textContent = episode.title;
  if (showEl) showEl.textContent = `${episode.show} · ${city.name}, ${city.country}`;

  if (markersEl) {
    markersEl.innerHTML = '';
    for (const m of episode.mentions) {
      const pct = runtime.durationSec > 0 ? Math.min(99, (m.sec / runtime.durationSec) * 100) : 0;
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.title = `${formatTime(m.sec)} — ${m.label}`;
      dot.style.left = `${pct}%`;
      dot.className =
        'pointer-events-auto absolute top-0 w-2 h-2.5 rounded-full bg-amber-400 hover:scale-125 transition -translate-x-1/2 cursor-pointer';
      dot.addEventListener('click', () => seekTo(m.sec));
      markersEl.appendChild(dot);
    }
  }

  const audio = document.getElementById('podatlas-audio-el') as HTMLAudioElement | null;
  const speed = SPEEDS[runtime.speedIdx];

  if (episode.audioUrl && audio) {
    runtime.isPreviewMode = false;
    stopPreviewTimer();
    if (audio.src !== episode.audioUrl) {
      audio.src = episode.audioUrl;
    }
    audio.currentTime = startSec;
    audio.playbackRate = speed;
    audio
      .play()
      .then(() => {
        runtime.isPlaying = true;
        if (hintEl) {
          hintEl.textContent =
            hintText || 'Streaming direct RSS audio · Yellow markers jump to extracted mentions';
        }
        updatePlayerUI();
      })
      .catch(() => {
        runtime.isPreviewMode = true;
        runtime.isPlaying = true;
        if (hintEl) {
          hintEl.textContent =
            hintText || 'Seed preview mode · Yellow markers jump to extracted Book/Place mentions';
        }
        startPreviewTimer();
        updatePlayerUI();
      });
  } else {
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
    }
    runtime.isPreviewMode = true;
    runtime.isPlaying = true;
    if (hintEl) {
      hintEl.textContent =
        hintText || 'Seed preview mode · Yellow markers jump to extracted Book/Place mentions';
    }
    startPreviewTimer();
    updatePlayerUI();
  }
}

function initPersistedControlsOnce(): void {
  if (runtime.initializedPersisted) return;
  runtime.initializedPersisted = true;

  // Audio player controls
  const audio = document.getElementById('podatlas-audio-el') as HTMLAudioElement | null;
  audio?.addEventListener('timeupdate', () => {
    if (!runtime.isPreviewMode && audio) {
      runtime.currentSec = Math.floor(audio.currentTime);
      updatePlayerUI();
    }
  });
  audio?.addEventListener('loadedmetadata', () => {
    if (audio && Number.isFinite(audio.duration) && audio.duration > 0) {
      runtime.durationSec = Math.floor(audio.duration);
      updatePlayerUI();
    }
  });
  audio?.addEventListener('ended', () => {
    runtime.isPlaying = false;
    updatePlayerUI();
  });

  document.getElementById('podatlas-play-pause')?.addEventListener('click', () => {
    if (!runtime.currentEpisode) {
      if (runtime.currentCity && runtime.currentCity.episodes.length > 0) {
        playEpisode(runtime.currentCity.episodes[0], runtime.currentCity, 0);
      }
      return;
    }
    if (runtime.isPreviewMode) {
      runtime.isPlaying = !runtime.isPlaying;
      if (runtime.isPlaying) startPreviewTimer();
      else stopPreviewTimer();
      updatePlayerUI();
      return;
    }
    if (!audio) return;
    if (runtime.isPlaying) {
      audio.pause();
      runtime.isPlaying = false;
      updatePlayerUI();
    } else {
      audio
        .play()
        .then(() => {
          runtime.isPlaying = true;
          updatePlayerUI();
        })
        .catch(() => {
          runtime.isPreviewMode = true;
          runtime.isPlaying = true;
          startPreviewTimer();
          updatePlayerUI();
        });
    }
  });

  document.getElementById('podatlas-player-scrubber')?.addEventListener('input', (e) => {
    seekTo(Number((e.target as HTMLInputElement).value));
  });
  document
    .getElementById('podatlas-skip-back')
    ?.addEventListener('click', () => seekTo(runtime.currentSec - 15));
  document
    .getElementById('podatlas-skip-fwd')
    ?.addEventListener('click', () => seekTo(runtime.currentSec + 30));

  const speedBtn = document.getElementById('podatlas-speed-btn');
  speedBtn?.addEventListener('click', () => {
    runtime.speedIdx = (runtime.speedIdx + 1) % SPEEDS.length;
    const speed = SPEEDS[runtime.speedIdx];
    if (speedBtn) speedBtn.textContent = `${speed}x`;
    if (audio) audio.playbackRate = speed;
    if (runtime.isPreviewMode && runtime.isPlaying) startPreviewTimer();
  });

  // Category filter buttons
  const catBar = document.getElementById('podatlas-category-bar');
  catBar?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-cat]');
    if (!btn) return;
    runtime.activeCategory = (btn.dataset.cat as CategoryFilter) || 'All';
    catBar.querySelectorAll<HTMLButtonElement>('[data-cat]').forEach((b) => {
      const active = b.dataset.cat === runtime.activeCategory;
      b.className = `cat-btn px-2.5 py-1 rounded-md text-xs font-medium transition cursor-pointer ${
        active
          ? 'bg-sky-500 text-slate-950 shadow-sm'
          : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
      }`;
    });
    syncMapFilterAndPills();
    const filtered = getFilteredGeoJson().features;
    if (
      filtered.length > 0 &&
      runtime.currentCity &&
      !filtered.some((f) => f.properties.id === runtime.currentCity?.id)
    ) {
      navigate(`/city/${encodeURIComponent(filtered[0].properties.id)}`);
    }
  });

  // Fuse.js instant search
  const searchInput = document.getElementById('podatlas-search-input') as HTMLInputElement | null;
  const searchClear = document.getElementById('podatlas-search-clear');
  const searchDropdown = document.getElementById('podatlas-search-dropdown');
  const searchBox = document.getElementById('podatlas-search-box');

  async function ensureSearchIndex(): Promise<void> {
    if (runtime.searchLoaded) return;
    runtime.searchLoaded = true;
    try {
      const res = await fetch('/search-index.json');
      if (!res.ok) return;
      const items = (await res.json()) as SearchIndexItem[];
      runtime.fuse = new Fuse(items, {
        keys: [
          { name: 'title', weight: 0.65 },
          { name: 'subtitle', weight: 0.3 },
          { name: 'kind', weight: 0.05 },
        ],
        threshold: 0.35,
        ignoreLocation: true,
      });
    } catch (err) {
      console.error('Failed to load search-index.json', err);
    }
  }

  function renderSearchResults(rawQuery: string): void {
    const trimmed = rawQuery.trim();
    searchClear?.classList.toggle('hidden', !trimmed);
    if (!trimmed || !searchDropdown) {
      searchDropdown?.classList.add('hidden');
      return;
    }
    const hits = runtime.fuse ? runtime.fuse.search(trimmed, { limit: 8 }).map((r) => r.item) : [];
    searchDropdown.innerHTML = '';
    if (hits.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'p-3 text-xs text-slate-400';
      empty.textContent = `No matches found for "${trimmed}"`;
      searchDropdown.appendChild(empty);
    } else {
      for (const item of hits) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className =
          'w-full text-left px-3 py-2 hover:bg-slate-800/80 border-b border-slate-800 last:border-0 flex items-center justify-between gap-2 transition cursor-pointer';
        btn.innerHTML = `
          <div class="min-w-0">
            <div class="text-xs font-medium text-slate-100 truncate"></div>
            <div class="text-[11px] text-slate-400 truncate"></div>
          </div>
          <span class="shrink-0 text-[10px] px-1.5 py-0.5 rounded border bg-slate-800 text-slate-300 border-slate-700 font-medium"></span>
        `;
        btn.querySelector('.text-slate-100')!.textContent = item.title;
        btn.querySelector('.text-slate-400')!.textContent = item.subtitle;
        btn.querySelector('span')!.textContent = item.kind;
        btn.addEventListener('click', () => {
          runtime.pendingTab = item.tab;
          searchDropdown.classList.add('hidden');
          if (searchInput) searchInput.value = '';
          searchClear?.classList.add('hidden');
          if (runtime.currentCity?.id === item.cityId) {
            activateDrawerTab(item.tab);
          } else {
            navigate(`/city/${encodeURIComponent(item.cityId)}`);
          }
        });
        searchDropdown.appendChild(btn);
      }
    }
    searchDropdown.classList.remove('hidden');
  }

  searchInput?.addEventListener('focus', () => {
    void ensureSearchIndex();
  });
  searchInput?.addEventListener('input', async (e) => {
    await ensureSearchIndex();
    renderSearchResults((e.target as HTMLInputElement).value);
  });
  searchClear?.addEventListener('click', () => {
    if (searchInput) searchInput.value = '';
    renderSearchResults('');
  });
  document.addEventListener('mousedown', (e) => {
    if (searchBox && !searchBox.contains(e.target as Node)) {
      searchDropdown?.classList.add('hidden');
    }
  });
}

// --- Per-Page Drawer & Modal Binding ---

function activateDrawerTab(tab: DrawerTab): void {
  document.querySelectorAll<HTMLButtonElement>('[data-tab-btn]').forEach((btn) => {
    const active = btn.dataset.tabBtn === tab;
    btn.className = `py-1.5 rounded-md text-xs font-medium transition cursor-pointer ${
      active ? 'bg-slate-800 text-slate-100 shadow-sm' : 'text-slate-400 hover:text-slate-200'
    }`;
  });
  document.querySelectorAll<HTMLElement>('[data-tab-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.tabPanel !== tab);
  });
}

function bindCurrentPageElements(): void {
  const city = readCurrentCityPayload();
  runtime.currentCity = city;

  // Drawer tabs
  const tabsContainer = document.getElementById('podatlas-drawer-tabs');
  tabsContainer?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-tab-btn]');
    if (!btn) return;
    activateDrawerTab((btn.dataset.tabBtn as DrawerTab) || 'episodes');
  });

  if (runtime.pendingTab) {
    activateDrawerTab(runtime.pendingTab);
    runtime.pendingTab = null;
  }

  // Episode play & timestamp jump buttons
  const drawer = document.getElementById('podatlas-city-drawer');
  drawer?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-play-ep]');
    if (!btn || !city) return;
    const epId = btn.dataset.playEp;
    const startSec = Number(btn.dataset.startSec || 0);
    const hint = btn.dataset.hint || '';
    const ep = city.episodes.find((item) => item.id === epId) || city.episodes[0];
    if (ep) {
      playEpisode(ep, city, startSec, hint);
    }
  });

  // Trip Pack Modal
  const modal = document.getElementById('podatlas-trip-modal');
  const openBtn = document.getElementById('podatlas-open-trip-pack');
  const closeBtn = document.getElementById('podatlas-close-trip-modal');
  const txtBtn = document.getElementById('podatlas-download-txt');
  const opmlBtn = document.getElementById('podatlas-download-opml');

  openBtn?.addEventListener('click', () => modal?.classList.remove('hidden'));
  closeBtn?.addEventListener('click', () => modal?.classList.add('hidden'));
  modal?.addEventListener('click', (e) => {
    if (e.target === modal) modal.classList.add('hidden');
  });

  txtBtn?.addEventListener('click', () => {
    if (!city) return;
    triggerFileDownload(
      `${city.id}-podatlas-trip-pack.txt`,
      buildTripItineraryText(city),
      'text/plain;charset=utf-8'
    );
  });

  opmlBtn?.addEventListener('click', () => {
    if (!city) return;
    triggerFileDownload(
      `${city.id}-podatlas-playlist.opml`,
      buildCityOpml(city),
      'text/x-opml;charset=utf-8'
    );
  });

  syncMapFilterAndPills();
  focusSelectedCityOnMap();
  updatePlayerUI();
}

document.addEventListener('astro:page-load', () => {
  initPersistedControlsOnce();
  void initMapOnce();
  bindCurrentPageElements();
});
