import { navigate } from 'astro:transitions/client';
import Fuse from 'fuse.js';
import maplibregl from 'maplibre-gl';
import type {
  CategoryFilter,
  CitiesGeoJSON,
  CityData,
  DrawerTab,
  EpisodeMention,
  EpisodeRecord,
  PlaceOrGearRecord,
  SearchIndexItem,
} from '../types/podatlas';
import {
  buildAmazonUrl,
  buildBookshopUrl,
  buildGoogleMapsUrl,
} from '../config/monetization';
import {
  buildCityKml,
  buildTripItineraryText,
  triggerFileDownload,
} from '../lib/exportPack';
import { CATEGORY_COLORS, formatTime } from '../lib/format';

const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';
const SPEEDS = [1, 1.25, 1.5, 2];
const MENTION_WINDOW_SEC = 75;

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

interface ClientRuntime {
  initializedPersisted: boolean;
  map: maplibregl.Map | null;
  mapReady: boolean;
  isGlobe: boolean;
  autoSpinEnabled: boolean;
  spinRafId: number | null;
  flightToastTimer: number | null;
  geoJson: CitiesGeoJSON | null;
  activeCategory: CategoryFilter;
  pendingTab: DrawerTab | null;
  pendingMobileView: 'map' | 'drawer' | null;
  fuse: Fuse<SearchIndexItem> | null;
  searchLoaded: boolean;
  currentCity: CityData | null;
  // Story Map state
  storyPlaces: PlaceOrGearRecord[];
  storyActiveName: string | null;
  storyModeActive: boolean;
  // Audio player state
  currentEpisode: EpisodeRecord | null;
  playingCity: CityData | null;
  isPlaying: boolean;
  isPreviewMode: boolean;
  currentSec: number;
  durationSec: number;
  speedIdx: number;
  previewTimer: number | null;
  lastActiveMentionKey: string | null;
}

const runtime: ClientRuntime = {
  initializedPersisted: false,
  map: null,
  mapReady: false,
  isGlobe: true,
  autoSpinEnabled: true,
  spinRafId: null,
  flightToastTimer: null,
  geoJson: null,
  activeCategory: 'All',
  pendingTab: null,
  pendingMobileView: null,
  fuse: null,
  searchLoaded: false,
  currentCity: null,
  storyPlaces: [],
  storyActiveName: null,
  storyModeActive: false,
  currentEpisode: null,
  playingCity: null,
  isPlaying: false,
  isPreviewMode: false,
  currentSec: 0,
  durationSec: 2880,
  speedIdx: 0,
  previewTimer: null,
  lastActiveMentionKey: null,
};

function isCityRoute(): boolean {
  return window.location.pathname.startsWith('/city/');
}

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

function syncMapFilter(): void {
  if (!runtime.geoJson) return;
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
    statsEl.textContent = `${features.length} ${features.length === 1 ? 'city' : 'cities'} · ${eps} episodes`;
  }

  // Filter welcome city cards if on welcome screen
  const visibleIds = new Set(filtered.features.map((f) => f.properties.id));
  document.querySelectorAll<HTMLAnchorElement>('[data-welcome-city-card]').forEach((card) => {
    const id = card.dataset.welcomeCityCard || '';
    card.classList.toggle('hidden', visibleIds.size > 0 && !visibleIds.has(id));
  });
}

function stopAutoSpin(): void {
  runtime.autoSpinEnabled = false;
  if (runtime.spinRafId !== null) {
    window.cancelAnimationFrame(runtime.spinRafId);
    runtime.spinRafId = null;
  }
}

function startAutoSpinIfEligible(): void {
  if (
    !runtime.map ||
    !runtime.mapReady ||
    !runtime.isGlobe ||
    !runtime.autoSpinEnabled ||
    isCityRoute() ||
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  ) {
    return;
  }
  if (runtime.spinRafId !== null) return;

  const spin = () => {
    if (!runtime.map || !runtime.autoSpinEnabled || !runtime.isGlobe || isCityRoute()) {
      runtime.spinRafId = null;
      return;
    }
    const center = runtime.map.getCenter();
    runtime.map.jumpTo({
      center: [center.lng + 0.032, center.lat],
    });
    runtime.spinRafId = window.requestAnimationFrame(spin);
  };
  runtime.spinRafId = window.requestAnimationFrame(spin);
}

function showFlightToast(label: string): void {
  const toast = document.getElementById('podatlas-flight-toast');
  const textEl = document.getElementById('podatlas-flight-toast-text');
  if (!toast || !textEl) return;

  textEl.textContent = label;
  toast.classList.remove('opacity-0', 'translate-y-2');
  toast.classList.add('opacity-100', 'translate-y-0');

  if (runtime.flightToastTimer !== null) {
    window.clearTimeout(runtime.flightToastTimer);
  }
  runtime.flightToastTimer = window.setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    toast.classList.remove('opacity-100', 'translate-y-0');
    runtime.flightToastTimer = null;
  }, 1900);
}

function setCityHalo(cityId: string): void {
  if (runtime.map && runtime.mapReady && runtime.map.getLayer('city-selected-halo')) {
    runtime.map.setFilter('city-selected-halo', ['==', ['get', 'id'], cityId]);
  }
}

function applyMapProjection(): void {
  if (!runtime.map || !runtime.mapReady) return;
  try {
    runtime.map.setProjection({ type: runtime.isGlobe ? 'globe' : 'mercator' });
    if (runtime.isGlobe && typeof runtime.map.setSky === 'function') {
      runtime.map.setSky({
        'sky-color': '#d8e4f0',
        'horizon-color': '#f3f0e9',
        'sky-horizon-blend': 0.55,
        'atmosphere-blend': 0.7,
      });
    }
  } catch {
    runtime.isGlobe = false;
  }

  const toggleBtn = document.getElementById('podatlas-projection-toggle');
  if (toggleBtn) {
    toggleBtn.setAttribute('aria-pressed', String(runtime.isGlobe));
    toggleBtn.title = runtime.isGlobe ? 'Switch to flat map' : 'Switch to 3D globe';
    toggleBtn.className = runtime.isGlobe
      ? 'w-9 h-9 rounded-xl bg-stone-900 text-white border border-stone-900 hover:bg-stone-800 flex items-center justify-center shadow-sm cursor-pointer transition'
      : 'w-9 h-9 rounded-xl bg-white/95 backdrop-blur border border-stone-200/90 text-stone-700 hover:bg-stone-100 hover:text-stone-900 flex items-center justify-center shadow-sm cursor-pointer transition';
  }

  if (runtime.isGlobe) {
    startAutoSpinIfEligible();
  } else {
    stopAutoSpin();
  }
}

function focusSelectedCityOnMap(shouldFly = true): void {
  if (!runtime.map || !runtime.mapReady) return;
  if (!isCityRoute() || !runtime.currentCity) {
    setCityHalo('');
    if (shouldFly) {
      runtime.map.flyTo({ center: [12, 22], zoom: 1.25, pitch: 0, duration: 900 });
    }
    startAutoSpinIfEligible();
    return;
  }

  stopAutoSpin();
  setCityHalo(runtime.currentCity.id);

  if (shouldFly) {
    showFlightToast(`Flying to ${runtime.currentCity.name}, ${runtime.currentCity.country}`);
    runtime.map.flyTo({
      center: [runtime.currentCity.lon, runtime.currentCity.lat],
      zoom: Math.max(runtime.map.getZoom(), 3.8),
      pitch: runtime.isGlobe ? 24 : 0,
      curve: 1.45,
      duration: 1100,
      essential: true,
    });
  }
}

function flyToRandomCity(): void {
  const features = getFilteredGeoJson().features;
  if (features.length === 0) return;
  const currentId = isCityRoute() ? runtime.currentCity?.id : '';
  const pool =
    features.length > 1 ? features.filter((f) => f.properties.id !== currentId) : features;
  const pick = pool[Math.floor(Math.random() * pool.length)];
  if (!pick) return;
  stopAutoSpin();
  navigate(`/city/${encodeURIComponent(pick.properties.id)}`);
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
    center: [12, 22],
    zoom: 1.25,
    minZoom: 0.75,
    maxZoom: 16,
    attributionControl: { compact: true },
  });

  const popup = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    offset: 14,
  });

  const storyPopup = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: true,
    offset: 16,
    maxWidth: '280px',
  });

  runtime.map = map;

  // Pause auto-spin immediately on any user map interaction
  map.on('mousedown', stopAutoSpin);
  map.on('touchstart', stopAutoSpin);
  map.on('wheel', stopAutoSpin);
  map.on('dragstart', stopAutoSpin);

  map.on('load', () => {
    runtime.mapReady = true;
    applyMapProjection();
    const initialData = getFilteredGeoJson();

    map.addSource('podatlas-cities', {
      type: 'geojson',
      data: initialData as unknown as GeoJSON.GeoJSON,
      cluster: true,
      clusterMaxZoom: 3,
      clusterRadius: 36,
    });

    map.addSource('podatlas-story-route', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addSource('podatlas-story-places', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    map.addLayer({
      id: 'city-clusters',
      type: 'circle',
      source: 'podatlas-cities',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#1c1917',
        'circle-radius': ['step', ['get', 'point_count'], 16, 5, 20, 15, 25],
        'circle-stroke-width': 2.5,
        'circle-stroke-color': '#ffffff',
        'circle-opacity': 0.92,
      },
    });

    map.addLayer({
      id: 'city-cluster-count',
      type: 'symbol',
      source: 'podatlas-cities',
      filter: ['has', 'point_count'],
      layout: { 'text-field': '{point_count_abbreviated}', 'text-size': 12 },
      paint: { 'text-color': '#ffffff' },
    });

    map.addLayer({
      id: 'city-selected-halo',
      type: 'circle',
      source: 'podatlas-cities',
      filter: ['==', ['get', 'id'], isCityRoute() ? (runtime.currentCity?.id ?? '') : ''],
      paint: {
        'circle-radius': 20,
        'circle-color': '#0284c7',
        'circle-opacity': 0.2,
        'circle-stroke-width': 2,
        'circle-stroke-color': '#0284c7',
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
          '#0284c7',
        ],
        'circle-radius': ['+', 6.5, ['min', 7, ['*', ['get', 'episodeCount'], 1.2]]],
        'circle-stroke-width': 2.5,
        'circle-stroke-color': '#ffffff',
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
        'text-offset': [0, 1.4],
        'text-anchor': 'top',
        'text-optional': true,
      },
      paint: {
        'text-color': '#1c1917',
        'text-halo-color': '#ffffff',
        'text-halo-width': 1.8,
      },
    });

    // --- In-Episode Story Map Layers ---
    map.addLayer({
      id: 'story-route-line',
      type: 'line',
      source: 'podatlas-story-route',
      layout: {
        'line-cap': 'round',
        'line-join': 'round',
      },
      paint: {
        'line-color': '#0284c7',
        'line-width': 2.5,
        'line-dasharray': [2, 2],
        'line-opacity': 0.7,
      },
    });

    map.addLayer({
      id: 'story-place-halo',
      type: 'circle',
      source: 'podatlas-story-places',
      filter: ['==', ['get', 'name'], ''],
      paint: {
        'circle-radius': 22,
        'circle-color': '#f59e0b',
        'circle-opacity': 0.28,
        'circle-stroke-width': 2.5,
        'circle-stroke-color': '#d97706',
      },
    });

    map.addLayer({
      id: 'story-place-pins',
      type: 'circle',
      source: 'podatlas-story-places',
      paint: {
        'circle-color': [
          'match',
          ['get', 'type'],
          'Food',
          '#e11d48',
          'Landmark',
          '#059669',
          '#0284c7',
        ],
        'circle-radius': 12,
        'circle-stroke-width': 2.5,
        'circle-stroke-color': '#ffffff',
      },
    });

    map.addLayer({
      id: 'story-place-numbers',
      type: 'symbol',
      source: 'podatlas-story-places',
      layout: {
        'text-field': ['to-string', ['get', 'index']],
        'text-size': 11,
        'text-allow-overlap': true,
      },
      paint: {
        'text-color': '#ffffff',
      },
    });

    map.addLayer({
      id: 'story-place-labels',
      type: 'symbol',
      source: 'podatlas-story-places',
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 12,
        'text-offset': [0, 1.5],
        'text-anchor': 'top',
        'text-optional': false,
      },
      paint: {
        'text-color': '#1c1917',
        'text-halo-color': '#ffffff',
        'text-halo-width': 2,
      },
    });

    map.on('click', 'story-place-pins', (e) => {
      stopAutoSpin();
      const feature = e.features?.[0];
      if (!feature) return;
      const geom = feature.geometry as GeoJSON.Point;
      const props = feature.properties;
      const epId = String(props.episodeId || '');
      const sec = Number(props.timestampSec || 0);
      const name = String(props.name || '');
      const note = String(props.note || '');
      const isUnesco = Boolean(props.isUnesco);
      const unescoId = String(props.unescoId || '');
      const unescoName = String(props.unescoName || '');
      const source = String(props.source || 'podcast');
      const cityName = String(props.cityName || runtime.currentCity?.name || '');
      const mapsUrl = buildGoogleMapsUrl({ name }, cityName);

      if (epId && runtime.currentEpisode && runtime.currentEpisode.id === epId) {
        seekTo(sec);
      }

      const badges: string[] = [];
      if (isUnesco) {
        const unescoTitle = escapeHtml(unescoName || 'UNESCO World Heritage Site');
        if (unescoId) {
          badges.push(
            `<a href="https://whc.unesco.org/en/list/${encodeURIComponent(unescoId)}" target="_blank" rel="noopener noreferrer" title="${unescoTitle}" style="display:inline-flex;align-items:center;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:9999px;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;text-decoration:none">UNESCO World Heritage</a>`
          );
        } else {
          badges.push(
            `<span title="${unescoTitle}" style="display:inline-flex;align-items:center;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:9999px;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe">UNESCO World Heritage</span>`
          );
        }
      }
      if (source === 'nps') {
        badges.push(
          `<span style="display:inline-flex;align-items:center;font-size:10.5px;font-weight:600;padding:1px 7px;border-radius:9999px;background:#f0fdfa;color:#115e59;border:1px solid #99f6e4">NPS</span>`
        );
      }
      const badgeRow =
        badges.length > 0
          ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">${badges.join('')}</div>`
          : '';
      const timeHtml = epId
        ? `<span style="font-family:monospace;color:#0369a1;font-weight:600">▶ ${formatTime(sec)}</span>`
        : '<span></span>';

      storyPopup
        .setLngLat(geom.coordinates as [number, number])
        .setHTML(
          `<div style="font-size:12.5px;line-height:1.45;color:#1c1917">
            <div style="font-weight:700">${escapeHtml(String(props.index ?? ''))}. ${escapeHtml(name)}</div>
            ${badgeRow}
            <div style="color:#57534e;margin-top:3px">${escapeHtml(note)}</div>
            <div style="margin-top:6px;display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11.5px">
              ${timeHtml}
              <a href="${escapeHtml(mapsUrl)}" target="_blank" rel="noopener noreferrer" style="color:#0284c7;font-weight:600;text-decoration:none">Google Maps ↗</a>
            </div>
          </div>`
        )
        .addTo(map);
    });

    map.on('mouseenter', 'story-place-pins', () => {
      map.getCanvas().style.cursor = 'pointer';
    });
    map.on('mouseleave', 'story-place-pins', () => {
      map.getCanvas().style.cursor = '';
    });

    map.on('click', 'city-clusters', async (e) => {
      stopAutoSpin();
      const features = map.queryRenderedFeatures(e.point, { layers: ['city-clusters'] });
      const clusterId = features[0]?.properties?.cluster_id;
      const source = map.getSource('podatlas-cities') as maplibregl.GeoJSONSource | undefined;
      if (!source || clusterId === undefined) return;
      const zoom = await source.getClusterExpansionZoom(clusterId);
      const geom = features[0].geometry as GeoJSON.Point;
      map.easeTo({ center: geom.coordinates as [number, number], zoom });
    });

    map.on('click', 'city-points', (e) => {
      stopAutoSpin();
      const feature = e.features?.[0];
      const cityId = feature?.properties?.id;
      if (cityId) {
        runtime.pendingMobileView = 'drawer';
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
          `<div style="font-size:12.5px;line-height:1.45">
            <div style="font-weight:600;color:#1c1917">${props.name}, ${props.country}</div>
            <div style="color:#57534e;margin-top:2px">${props.episodeCount} episodes · ${props.bookCount} books · ${props.placeCount} places</div>
            <div style="color:#0284c7;margin-top:4px;font-weight:600;font-size:11.5px">Explore city →</div>
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

    syncMapFilter();
    focusSelectedCityOnMap(isCityRoute());
  });

  document.getElementById('podatlas-zoom-in')?.addEventListener('click', () => {
    stopAutoSpin();
    map.zoomIn();
  });
  document.getElementById('podatlas-zoom-out')?.addEventListener('click', () => {
    stopAutoSpin();
    map.zoomOut();
  });
  document.getElementById('podatlas-zoom-reset')?.addEventListener('click', () => {
    stopAutoSpin();
    clearStoryMap(false);
    map.flyTo({ center: [12, 22], zoom: 1.25, pitch: 0, duration: 850 });
  });
  document.getElementById('podatlas-projection-toggle')?.addEventListener('click', () => {
    runtime.isGlobe = !runtime.isGlobe;
    applyMapProjection();
  });
  document.getElementById('podatlas-exit-story-btn')?.addEventListener('click', () => {
    clearStoryMap(true);
  });
}

// --- In-Episode Story Map Mode ---

function setStoryPill(visible: boolean, label?: string): void {
  const pill = document.getElementById('podatlas-story-pill');
  const textEl = document.getElementById('podatlas-story-pill-label');
  if (!pill) return;
  pill.classList.toggle('hidden', !visible);
  pill.classList.toggle('flex', visible);
  if (label && textEl) {
    textEl.textContent = label;
  }
}

function clearStoryMap(flyBack = false): void {
  runtime.storyPlaces = [];
  runtime.storyActiveName = null;
  runtime.storyModeActive = false;
  setStoryPill(false);

  if (runtime.map && runtime.mapReady) {
    const placesSrc = runtime.map.getSource('podatlas-story-places') as
      | maplibregl.GeoJSONSource
      | undefined;
    const routeSrc = runtime.map.getSource('podatlas-story-route') as
      | maplibregl.GeoJSONSource
      | undefined;
    const empty: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
    placesSrc?.setData(empty);
    routeSrc?.setData(empty);
    if (runtime.map.getLayer('story-place-halo')) {
      runtime.map.setFilter('story-place-halo', ['==', ['get', 'name'], '']);
    }
    if (flyBack) {
      focusSelectedCityOnMap(true);
    }
  }
}

function activateStoryMap(
  episode: EpisodeRecord | null,
  city: CityData,
  focusCoord?: { lat: number; lon: number; name: string }
): void {
  const episodePlaces = episode
    ? city.placesAndGear.filter(
        (p) => p.type !== 'Gear' && p.lat != null && p.lon != null && p.episodeId === episode.id
      )
    : [];
  const cityPlaces = city.placesAndGear.filter(
    (p) => p.type !== 'Gear' && p.lat != null && p.lon != null
  );
  const stops = (episodePlaces.length > 0 ? episodePlaces : cityPlaces).sort(
    (a, b) => a.timestampSec - b.timestampSec
  );

  if (stops.length === 0) {
    clearStoryMap(false);
    return;
  }

  runtime.storyPlaces = stops;
  runtime.storyModeActive = true;
  runtime.storyActiveName = focusCoord?.name ?? stops[0]?.name ?? null;

  const features: GeoJSON.Feature<GeoJSON.Point>[] = stops.map((p, idx) => ({
    type: 'Feature',
    geometry: {
      type: 'Point',
      coordinates: [p.lon as number, p.lat as number],
    },
    properties: {
      index: idx + 1,
      name: p.name,
      type: p.type,
      note: p.note,
      timestampSec: p.timestampSec,
      episodeId: p.episodeId,
      cityName: city.name,
      isUnesco: Boolean(p.isUnesco),
      unescoId: p.unescoId ?? '',
      unescoName: p.unescoName ?? '',
      source: p.source ?? 'podcast',
    },
  }));

  const routeFeatures: GeoJSON.Feature<GeoJSON.LineString>[] =
    stops.length >= 2
      ? [
          {
            type: 'Feature',
            geometry: {
              type: 'LineString',
              coordinates: stops.map((p) => [p.lon as number, p.lat as number]),
            },
            properties: {},
          },
        ]
      : [];

  setStoryPill(
    true,
    `Story Map · ${stops.length} ${stops.length === 1 ? 'stop' : 'stops'} in ${city.name}`
  );

  if (!runtime.map || !runtime.mapReady) return;
  stopAutoSpin();

  const placesSrc = runtime.map.getSource('podatlas-story-places') as
    | maplibregl.GeoJSONSource
    | undefined;
  const routeSrc = runtime.map.getSource('podatlas-story-route') as
    | maplibregl.GeoJSONSource
    | undefined;
  placesSrc?.setData({ type: 'FeatureCollection', features });
  routeSrc?.setData({ type: 'FeatureCollection', features: routeFeatures });

  if (runtime.map.getLayer('story-place-halo') && runtime.storyActiveName) {
    runtime.map.setFilter('story-place-halo', ['==', ['get', 'name'], runtime.storyActiveName]);
  }

  if (focusCoord) {
    runtime.map.flyTo({
      center: [focusCoord.lon, focusCoord.lat],
      zoom: 14.5,
      pitch: runtime.isGlobe ? 30 : 0,
      duration: 950,
      essential: true,
    });
  } else if (stops.length === 1) {
    runtime.map.flyTo({
      center: [stops[0].lon as number, stops[0].lat as number],
      zoom: 13.5,
      pitch: runtime.isGlobe ? 26 : 0,
      duration: 1000,
      essential: true,
    });
  } else {
    const bounds = new maplibregl.LngLatBounds();
    for (const p of stops) {
      bounds.extend([p.lon as number, p.lat as number]);
    }
    runtime.map.fitBounds(bounds, {
      padding: { top: 96, bottom: 80, left: 64, right: 64 },
      maxZoom: 14.2,
      duration: 1050,
      essential: true,
    });
  }
}

function syncActiveStoryPlace(): void {
  if (!runtime.storyModeActive || runtime.storyPlaces.length === 0) return;
  const stops = runtime.storyPlaces;

  let activeIdx = -1;
  for (let i = 0; i < stops.length; i += 1) {
    const p = stops[i];
    if (runtime.currentSec >= p.timestampSec && runtime.currentSec <= p.timestampSec + MENTION_WINDOW_SEC) {
      activeIdx = i;
    }
  }
  if (activeIdx === -1) {
    for (let i = stops.length - 1; i >= 0; i -= 1) {
      if (runtime.currentSec >= stops[i].timestampSec) {
        activeIdx = i;
        break;
      }
    }
  }

  const activePlace = activeIdx >= 0 ? stops[activeIdx] : stops[0];
  if (!activePlace || runtime.storyActiveName === activePlace.name) return;
  runtime.storyActiveName = activePlace.name;

  setStoryPill(
    true,
    `Story Map · Stop ${activeIdx >= 0 ? activeIdx + 1 : 1}/${stops.length}: ${activePlace.name}`
  );

  if (runtime.map && runtime.mapReady && runtime.map.getLayer('story-place-halo')) {
    runtime.map.setFilter('story-place-halo', ['==', ['get', 'name'], activePlace.name]);
  }
}

// --- Persistent On-Demand Audio Player + Live Mention Sync ---

function setPlayerVisible(visible: boolean): void {
  const bar = document.getElementById('podatlas-player-bar');
  bar?.classList.toggle('hidden', !visible);
  window.requestAnimationFrame(() => {
    runtime.map?.resize();
  });
}

function findActiveMention(): EpisodeMention | null {
  const ep = runtime.currentEpisode;
  if (!ep || ep.mentions.length === 0) return null;
  let active: EpisodeMention | null = null;
  for (const m of ep.mentions) {
    if (runtime.currentSec >= m.sec && runtime.currentSec <= m.sec + MENTION_WINDOW_SEC) {
      if (!active || m.sec > active.sec) {
        active = m;
      }
    }
  }
  return active;
}

function resolveMentionAction(
  mention: EpisodeMention,
  city: CityData | null
): { label: string; url: string } | null {
  if (!city) return null;
  const needle = mention.label.toLowerCase();

  if (mention.type === 'book') {
    const book = city.books.find((b) => b.title.toLowerCase() === needle);
    if (book) return { label: 'Bookshop.org ↗', url: buildBookshopUrl(book) };
    return { label: 'Amazon ↗', url: buildAmazonUrl(mention.label) };
  }

  if (mention.type === 'gear') {
    return { label: 'View on Amazon ↗', url: buildAmazonUrl(mention.label) };
  }

  const place = city.placesAndGear.find((p) => p.name.toLowerCase() === needle);
  if (place) {
    return { label: 'Open in Maps ↗', url: buildGoogleMapsUrl(place, city.name) };
  }
  return {
    label: 'Open in Maps ↗',
    url: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${mention.label} ${city.name}`)}`,
  };
}

function syncActiveMentionUI(): void {
  const active = findActiveMention();
  const epId = runtime.currentEpisode?.id ?? '';
  const activeKey = active ? `${epId}:${active.sec}:${active.label}` : 'none';

  // Highlight active mention chip in drawer
  document.querySelectorAll<HTMLButtonElement>('.mention-chip').forEach((chip) => {
    const isMatch =
      Boolean(active) &&
      chip.dataset.mentionEp === epId &&
      Number(chip.dataset.mentionSec) === active?.sec;
    chip.classList.toggle('border-amber-400', isMatch);
    chip.classList.toggle('bg-amber-50', isMatch);
    chip.classList.toggle('ring-1', isMatch);
    chip.classList.toggle('ring-amber-400/40', isMatch);
    chip.classList.toggle('bg-stone-50', !isMatch);
    chip.classList.toggle('border-stone-200/90', !isMatch);
  });

  if (runtime.lastActiveMentionKey === activeKey) return;
  runtime.lastActiveMentionKey = activeKey;

  const hintEl = document.getElementById('podatlas-player-hint');
  if (!hintEl) return;

  hintEl.innerHTML = '';

  if (active) {
    const wrap = document.createElement('span');
    wrap.className = 'inline-flex items-center gap-2 min-w-0';

    const badge = document.createElement('span');
    badge.className =
      'shrink-0 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-semibold uppercase tracking-wide';
    badge.textContent = 'Mentioned now';

    const title = document.createElement('span');
    title.className = 'font-medium text-stone-800 truncate';
    title.textContent = active.label;

    wrap.appendChild(badge);
    wrap.appendChild(title);

    const action = resolveMentionAction(active, runtime.playingCity);
    if (action) {
      const link = document.createElement('a');
      link.href = action.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.className = 'shrink-0 font-semibold text-sky-700 hover:underline';
      link.textContent = action.label;
      wrap.appendChild(link);
    }

    hintEl.appendChild(wrap);
  } else {
    hintEl.textContent = runtime.isPreviewMode
      ? 'Preview timeline · Click any amber dot on the bar to jump to a mention'
      : 'Click any amber dot on the bar to jump to a mention';
  }
}

function updatePlayerUI(): void {
  const playIcon = document.getElementById('podatlas-icon-play');
  const pauseIcon = document.getElementById('podatlas-icon-pause');
  const eqBars = document.getElementById('podatlas-eq-bars');
  playIcon?.classList.toggle('hidden', runtime.isPlaying);
  pauseIcon?.classList.toggle('hidden', !runtime.isPlaying);
  eqBars?.classList.toggle('hidden', !runtime.isPlaying);

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
    card.classList.toggle('border-sky-600', active);
    card.classList.toggle('ring-1', active);
    card.classList.toggle('ring-sky-600/20', active);
    card.classList.toggle('border-stone-200/90', !active);
  });

  syncActiveMentionUI();
  syncActiveStoryPlace();
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

function closePlayer(): void {
  const audio = document.getElementById('podatlas-audio-el') as HTMLAudioElement | null;
  if (audio) {
    audio.pause();
  }
  stopPreviewTimer();
  runtime.isPlaying = false;
  runtime.currentEpisode = null;
  runtime.playingCity = null;
  runtime.lastActiveMentionKey = null;
  clearStoryMap(false);
  setPlayerVisible(false);
  updatePlayerUI();
}

function togglePlayPause(): void {
  const audio = document.getElementById('podatlas-audio-el') as HTMLAudioElement | null;
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
}

function playEpisode(episode: EpisodeRecord, city: CityData, startSec = 0): void {
  runtime.currentEpisode = episode;
  runtime.playingCity = city;
  runtime.currentSec = startSec;
  runtime.durationSec = episode.durationSec || 2400;
  runtime.lastActiveMentionKey = null;
  setPlayerVisible(true);
  activateStoryMap(episode, city);

  const titleEl = document.getElementById('podatlas-player-title');
  const showEl = document.getElementById('podatlas-player-show');
  const markersEl = document.getElementById('podatlas-mention-markers');

  if (titleEl) titleEl.textContent = episode.title;
  if (showEl) {
    showEl.textContent = episode.show
      ? `${episode.show} · ${city.name}, ${city.country}`
      : `${city.name}, ${city.country}`;
  }

  if (markersEl) {
    markersEl.innerHTML = '';
    for (const m of episode.mentions) {
      const pct = runtime.durationSec > 0 ? Math.min(99, (m.sec / runtime.durationSec) * 100) : 0;
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.title = `${formatTime(m.sec)} — ${m.label}`;
      dot.style.left = `${pct}%`;
      dot.className =
        'pointer-events-auto absolute top-0 w-2.5 h-2.5 rounded-full bg-amber-500 ring-2 ring-white hover:scale-125 transition -translate-x-1/2 cursor-pointer';
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
        updatePlayerUI();
      })
      .catch(() => {
        runtime.isPreviewMode = true;
        runtime.isPlaying = true;
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

  document.getElementById('podatlas-play-pause')?.addEventListener('click', togglePlayPause);

  document.querySelectorAll<HTMLButtonElement>('[data-player-close-btn]').forEach((btn) => {
    btn.addEventListener('click', () => closePlayer());
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

  const speedBtns = document.querySelectorAll<HTMLButtonElement>('[data-player-speed-btn]');
  speedBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      runtime.speedIdx = (runtime.speedIdx + 1) % SPEEDS.length;
      const speed = SPEEDS[runtime.speedIdx];
      speedBtns.forEach((b) => {
        b.textContent = `${speed}x`;
      });
      if (audio) audio.playbackRate = speed;
      if (runtime.isPreviewMode && runtime.isPlaying) startPreviewTimer();
    });
  });

  // Delegated Surprise Me triggers (map pill + welcome hero)
  document.addEventListener('click', (e) => {
    const trigger = (e.target as HTMLElement).closest('[data-surprise-trigger]');
    if (trigger) {
      flyToRandomCity();
    }
  });

  // Category filter buttons
  const catBar = document.getElementById('podatlas-category-bar');
  catBar?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-cat]');
    if (!btn) return;
    runtime.activeCategory = (btn.dataset.cat as CategoryFilter) || 'All';
    catBar.querySelectorAll<HTMLButtonElement>('[data-cat]').forEach((b) => {
      const active = b.dataset.cat === runtime.activeCategory;
      b.className = `cat-btn shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border transition cursor-pointer ${
        active
          ? 'bg-stone-900 text-white border-stone-900 shadow-2xs'
          : 'bg-white text-stone-700 border-stone-200 hover:bg-stone-100 hover:border-stone-300'
      }`;
    });
    syncMapFilter();
    const filtered = getFilteredGeoJson().features;
    if (
      isCityRoute() &&
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
  const searchKbd = document.getElementById('podatlas-search-kbd');
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
    searchKbd?.classList.toggle('md:inline-flex', !trimmed);
    searchKbd?.classList.toggle('hidden', Boolean(trimmed));
    if (!trimmed || !searchDropdown) {
      searchDropdown?.classList.add('hidden');
      return;
    }
    const hits = runtime.fuse ? runtime.fuse.search(trimmed, { limit: 8 }).map((r) => r.item) : [];
    searchDropdown.innerHTML = '';
    if (hits.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'p-3.5 text-xs text-stone-500';
      empty.textContent = `No matches found for "${trimmed}"`;
      searchDropdown.appendChild(empty);
    } else {
      for (const item of hits) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className =
          'w-full text-left px-3.5 py-2.5 hover:bg-stone-50 border-b border-stone-100 last:border-0 flex items-center justify-between gap-2.5 transition cursor-pointer';
        btn.innerHTML = `
          <div class="min-w-0">
            <div class="text-[13px] font-medium text-stone-900 truncate"></div>
            <div class="text-xs text-stone-500 truncate mt-0.5"></div>
          </div>
          <span class="shrink-0 text-[11px] px-2 py-0.5 rounded-full bg-stone-100 text-stone-600 font-medium"></span>
        `;
        btn.querySelector('.text-stone-900')!.textContent = item.title;
        btn.querySelector('.text-stone-500')!.textContent = item.subtitle;
        btn.querySelector('span')!.textContent = item.kind;
        btn.addEventListener('click', () => {
          runtime.pendingTab = item.tab;
          runtime.pendingMobileView = 'drawer';
          searchDropdown.classList.add('hidden');
          if (searchInput) searchInput.value = '';
          searchClear?.classList.add('hidden');
          searchKbd?.classList.add('md:inline-flex');
          if (isCityRoute() && runtime.currentCity?.id === item.cityId) {
            activateDrawerTab(item.tab);
            setMobileView('drawer');
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

  // Global keyboard shortcuts (/, Escape, Space, R)
  document.addEventListener('keydown', (e) => {
    const target = e.target as HTMLElement | null;
    const inInput =
      target?.tagName === 'INPUT' ||
      target?.tagName === 'TEXTAREA' ||
      target?.isContentEditable;

    if (e.key === '/' && !inInput) {
      e.preventDefault();
      searchInput?.focus();
      return;
    }
    if (e.key === 'Escape' && document.activeElement === searchInput) {
      searchDropdown?.classList.add('hidden');
      searchInput?.blur();
      return;
    }
    if (!inInput && e.key === ' ' && runtime.currentEpisode) {
      e.preventDefault();
      togglePlayPause();
      return;
    }
    if (!inInput && (e.key === 'r' || e.key === 'R') && !e.metaKey && !e.ctrlKey && !e.altKey) {
      flyToRandomCity();
    }
  });
}

// --- Per-Page Drawer & Mobile View Binding ---

function setMobileView(view: 'map' | 'drawer'): void {
  const mapPane = document.getElementById('podatlas-map-pane');
  const drawer = document.getElementById('podatlas-city-drawer');
  const toggleBar = document.getElementById('podatlas-mobile-toggle');

  if (view === 'drawer') {
    mapPane?.classList.add('hidden', 'md:block');
    drawer?.classList.remove('hidden');
    drawer?.classList.add('flex');
  } else {
    mapPane?.classList.remove('hidden');
    drawer?.classList.add('hidden', 'md:flex');
    window.requestAnimationFrame(() => runtime.map?.resize());
  }

  toggleBar?.querySelectorAll<HTMLButtonElement>('[data-mobile-view]').forEach((btn) => {
    const active = btn.dataset.mobileView === view;
    btn.className = `flex-1 py-2 px-2 rounded-lg text-xs truncate transition cursor-pointer ${
      active
        ? 'font-semibold bg-stone-900 text-white'
        : 'font-medium bg-stone-100 text-stone-700 hover:bg-stone-200'
    }`;
  });
}

function activateDrawerTab(tab: DrawerTab): void {
  document.querySelectorAll<HTMLButtonElement>('[data-tab-btn]').forEach((btn) => {
    const active = btn.dataset.tabBtn === tab;
    btn.className = `py-2 px-1 rounded-lg text-[11.5px] sm:text-xs whitespace-nowrap transition cursor-pointer ${
      active
        ? 'font-semibold bg-white text-stone-900 shadow-xs'
        : 'font-medium text-stone-600 hover:text-stone-900'
    }`;
  });
  document.querySelectorAll<HTMLElement>('[data-tab-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.tabPanel !== tab);
  });
}

function bindCurrentPageElements(): void {
  const city = readCurrentCityPayload();
  runtime.currentCity = city;

  // Drawer tabs (on /city/:slug)
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

  // Mobile toggle
  const mobileToggle = document.getElementById('podatlas-mobile-toggle');
  mobileToggle?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-mobile-view]');
    if (!btn) return;
    setMobileView(btn.dataset.mobileView === 'drawer' ? 'drawer' : 'map');
  });

  if (runtime.pendingMobileView) {
    setMobileView(runtime.pendingMobileView);
    runtime.pendingMobileView = null;
  } else {
    const defaultView =
      mobileToggle?.dataset.defaultMobileView === 'drawer' ? 'drawer' : 'map';
    setMobileView(defaultView);
  }

  // Welcome city card hover halo preview & mobile navigation intent
  document.querySelectorAll<HTMLAnchorElement>('[data-welcome-city-card]').forEach((card) => {
    const cityId = card.dataset.welcomeCityCard || '';
    card.addEventListener('mouseenter', () => setCityHalo(cityId));
    card.addEventListener('mouseleave', () => {
      if (!isCityRoute()) setCityHalo('');
    });
    card.addEventListener('click', () => {
      runtime.pendingMobileView = 'drawer';
    });
  });

  // Episode play, timestamp jump, and "Show on map" place focus buttons
  const drawer = document.getElementById('podatlas-city-drawer');
  drawer?.addEventListener('click', (e) => {
    const focusBtn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-focus-place-lat]');
    if (focusBtn && city) {
      const lat = Number(focusBtn.dataset.focusPlaceLat);
      const lon = Number(focusBtn.dataset.focusPlaceLon);
      const name = focusBtn.dataset.focusPlaceName || '';
      const epId = focusBtn.dataset.focusPlaceEp || '';
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        const ep = epId ? city.episodes.find((item) => item.id === epId) || null : null;
        setMobileView('map');
        activateStoryMap(ep, city, { lat, lon, name });
        showFlightToast(`Zooming to ${name}`);
      }
      return;
    }

    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-play-ep]');
    if (!btn || !city) return;
    const epId = btn.dataset.playEp;
    if (!epId) return;
    const startSec = Number(btn.dataset.startSec || 0);
    const ep = city.episodes.find((item) => item.id === epId);
    if (ep) {
      playEpisode(ep, city, startSec);
    }
  });

  // Trip Pack Modal (RSS Feed copy + Google Maps KML + Itinerary TXT)
  const tripModal = document.getElementById('podatlas-trip-modal');
  const openTripBtn = document.getElementById('podatlas-open-trip-modal');
  const closeTripBtn = document.getElementById('podatlas-close-trip-modal');
  const rssInput = document.getElementById('podatlas-rss-url-input') as HTMLInputElement | null;
  const copyRssBtn = document.getElementById('podatlas-copy-rss');
  const downloadKmlBtn = document.getElementById('podatlas-download-kml');
  const downloadTxtBtn = document.getElementById('podatlas-download-txt');

  if (rssInput && rssInput.dataset.feedPath) {
    rssInput.value = `${window.location.origin}${rssInput.dataset.feedPath}`;
  }

  openTripBtn?.addEventListener('click', () => {
    tripModal?.classList.remove('hidden');
  });
  closeTripBtn?.addEventListener('click', () => {
    tripModal?.classList.add('hidden');
  });
  tripModal?.addEventListener('click', (e) => {
    if (e.target === tripModal) {
      tripModal.classList.add('hidden');
    }
  });

  copyRssBtn?.addEventListener('click', async () => {
    if (!rssInput) return;
    const url = rssInput.value;
    try {
      await navigator.clipboard.writeText(url);
      copyRssBtn.textContent = 'Copied!';
      window.setTimeout(() => {
        copyRssBtn.textContent = 'Copy RSS';
      }, 1800);
    } catch {
      rssInput.select();
      document.execCommand('copy');
      copyRssBtn.textContent = 'Copied!';
      window.setTimeout(() => {
        copyRssBtn.textContent = 'Copy RSS';
      }, 1800);
    }
  });

  downloadKmlBtn?.addEventListener('click', () => {
    if (!city) return;
    const kml = buildCityKml(city);
    triggerFileDownload(
      `${city.id}-pins.kml`,
      kml,
      'application/vnd.google-earth.kml+xml;charset=utf-8'
    );
  });

  downloadTxtBtn?.addEventListener('click', () => {
    if (!city) return;
    const txt = buildTripItineraryText(city);
    triggerFileDownload(`${city.id}-trip-pack.txt`, txt, 'text/plain;charset=utf-8');
  });

  if (!isCityRoute() || (runtime.playingCity && runtime.playingCity.id !== city?.id)) {
    clearStoryMap(false);
  }

  syncMapFilter();
  focusSelectedCityOnMap(true);
  updatePlayerUI();
}

document.addEventListener('astro:page-load', () => {
  initPersistedControlsOnce();
  void initMapOnce();
  bindCurrentPageElements();
});
