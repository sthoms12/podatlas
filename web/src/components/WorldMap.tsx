import { useEffect, useMemo, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import { Minus, Plus, RotateCcw } from 'lucide-react';
import type { CategoryFilter, CitiesGeoJSON, CityGeoJSONFeature } from '../types/podatlas';
import { CATEGORY_COLORS } from '../lib/format';

// 100% open-source, free vector tiles from OpenFreeMap ($0 tile cost, no API key)
const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/positron';

interface WorldMapProps {
  geoJson: CitiesGeoJSON | null;
  activeCategory: CategoryFilter;
  selectedCityId: string | null;
  onSelectCity: (cityId: string) => void;
}

export function WorldMap({
  geoJson,
  activeCategory,
  selectedCityId,
  onSelectCity,
}: WorldMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const isMapReadyRef = useRef(false);
  const onSelectCityRef = useRef(onSelectCity);
  onSelectCityRef.current = onSelectCity;

  const filteredGeoJson = useMemo<CitiesGeoJSON>(() => {
    if (!geoJson) return { type: 'FeatureCollection', features: [] };
    if (activeCategory === 'All') return geoJson;
    return {
      type: 'FeatureCollection',
      features: geoJson.features.filter(
        (f) =>
          f.properties.primaryCategory === activeCategory ||
          f.properties.categories.includes(activeCategory)
      ),
    };
  }, [geoJson, activeCategory]);

  const stats = useMemo(() => {
    const features = filteredGeoJson.features;
    return {
      cities: features.length,
      episodes: features.reduce((sum, f) => sum + f.properties.episodeCount, 0),
      books: features.reduce((sum, f) => sum + f.properties.bookCount, 0),
      places: features.reduce((sum, f) => sum + f.properties.placeCount, 0),
    };
  }, [filteredGeoJson]);

  // Initialize MapLibre GL JS instance once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
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
    popupRef.current = popup;
    mapRef.current = map;

    map.on('load', () => {
      isMapReadyRef.current = true;

      map.addSource('podatlas-cities', {
        type: 'geojson',
        data: filteredGeoJson as unknown as GeoJSON.GeoJSON,
        cluster: true,
        clusterMaxZoom: 3,
        clusterRadius: 36,
      });

      // Cluster circles
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
        layout: {
          'text-field': '{point_count_abbreviated}',
          'text-size': 12,
        },
        paint: {
          'text-color': '#020617',
        },
      });

      // Selected city highlight halo
      map.addLayer({
        id: 'city-selected-halo',
        type: 'circle',
        source: 'podatlas-cities',
        filter: ['==', ['get', 'id'], selectedCityId || ''],
        paint: {
          'circle-radius': 18,
          'circle-color': '#38bdf8',
          'circle-opacity': 0.28,
          'circle-stroke-width': 2,
          'circle-stroke-color': '#38bdf8',
        },
      });

      // Individual city pins colored by primaryCategory
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

      // City labels
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

      // Click cluster to zoom in
      map.on('click', 'city-clusters', async (e) => {
        const features = map.queryRenderedFeatures(e.point, { layers: ['city-clusters'] });
        const clusterId = features[0]?.properties?.cluster_id;
        const source = map.getSource('podatlas-cities') as maplibregl.GeoJSONSource | undefined;
        if (!source || clusterId === undefined) return;
        const zoom = await source.getClusterExpansionZoom(clusterId);
        const geom = features[0].geometry as GeoJSON.Point;
        map.easeTo({ center: geom.coordinates as [number, number], zoom });
      });

      // Click city pin to open drawer
      map.on('click', 'city-points', (e) => {
        const feature = e.features?.[0];
        const cityId = feature?.properties?.id;
        if (cityId) onSelectCityRef.current(String(cityId));
      });

      // Hover tooltip
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
    });

    return () => {
      isMapReadyRef.current = false;
      popup.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Update GeoJSON source when category filter or data changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReadyRef.current) return;
    const source = map.getSource('podatlas-cities') as maplibregl.GeoJSONSource | undefined;
    if (source) {
      source.setData(filteredGeoJson as unknown as GeoJSON.GeoJSON);
    }
  }, [filteredGeoJson]);

  // Highlight & fly to selected city
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isMapReadyRef.current) return;
    if (map.getLayer('city-selected-halo')) {
      map.setFilter('city-selected-halo', ['==', ['get', 'id'], selectedCityId || '']);
    }
    if (!selectedCityId || !geoJson) return;
    const target: CityGeoJSONFeature | undefined = geoJson.features.find(
      (f) => f.properties.id === selectedCityId
    );
    if (target) {
      map.flyTo({
        center: target.geometry.coordinates,
        zoom: Math.max(map.getZoom(), 3.6),
        duration: 900,
        essential: true,
      });
    }
  }, [selectedCityId, geoJson]);

  const handleReset = () => {
    mapRef.current?.flyTo({ center: [12, 26], zoom: 1.75, duration: 750 });
  };

  return (
    <main className="relative flex-1 min-w-0 bg-slate-950 overflow-hidden">
      <div ref={containerRef} className="w-full h-full" />

      {/* Floating Stats Pill */}
      <div className="absolute top-3 left-3 flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-900/90 backdrop-blur border border-slate-700/80 text-xs text-slate-200 shadow-lg pointer-events-none">
        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
        <span className="font-medium">
          {stats.cities} Cities · {stats.episodes} Episodes · {stats.books} Books · {stats.places}{' '}
          Places
        </span>
      </div>

      {/* Map Controls */}
      <div className="absolute top-3 right-3 flex flex-col gap-1.5">
        <button
          type="button"
          onClick={() => mapRef.current?.zoomIn()}
          title="Zoom in"
          className="w-8 h-8 rounded-lg bg-slate-900/90 backdrop-blur border border-slate-700 text-slate-100 hover:bg-slate-800 flex items-center justify-center shadow-md cursor-pointer"
        >
          <Plus className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => mapRef.current?.zoomOut()}
          title="Zoom out"
          className="w-8 h-8 rounded-lg bg-slate-900/90 backdrop-blur border border-slate-700 text-slate-100 hover:bg-slate-800 flex items-center justify-center shadow-md cursor-pointer"
        >
          <Minus className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={handleReset}
          title="Reset world view"
          className="w-8 h-8 rounded-lg bg-slate-900/90 backdrop-blur border border-slate-700 text-slate-100 hover:bg-slate-800 flex items-center justify-center shadow-md cursor-pointer"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Bottom Quick-Jump City Strip */}
      <div className="absolute bottom-3 left-3 right-3 flex items-center gap-1.5 overflow-x-auto custom-scroll pb-1">
        <span className="shrink-0 text-xs text-slate-300 bg-slate-900/90 backdrop-blur px-2.5 py-1 rounded-full border border-slate-700/80">
          Explore:
        </span>
        <div className="flex items-center gap-1.5">
          {filteredGeoJson.features.map((feat) => {
            const p = feat.properties;
            const active = p.id === selectedCityId;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => onSelectCity(p.id)}
                className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-medium border transition cursor-pointer ${
                  active
                    ? 'bg-sky-500 text-slate-950 border-sky-400 shadow-sm'
                    : 'bg-slate-900/90 backdrop-blur text-slate-200 border-slate-700/80 hover:bg-slate-800'
                }`}
              >
                {p.name}
              </button>
            );
          })}
        </div>
      </div>
    </main>
  );
}
