import type { CitiesGeoJSON, CityData, SearchIndexItem } from '../types/podatlas';

const cityCache = new Map<string, CityData>();
let geoJsonPromise: Promise<CitiesGeoJSON> | null = null;
let searchIndexPromise: Promise<SearchIndexItem[]> | null = null;

export function fetchCitiesGeoJSON(): Promise<CitiesGeoJSON> {
  if (!geoJsonPromise) {
    geoJsonPromise = fetch('/cities.geojson').then((res) => {
      if (!res.ok) throw new Error(`Failed to load cities.geojson (${res.status})`);
      return res.json() as Promise<CitiesGeoJSON>;
    });
  }
  return geoJsonPromise;
}

export function fetchSearchIndex(): Promise<SearchIndexItem[]> {
  if (!searchIndexPromise) {
    searchIndexPromise = fetch('/search-index.json').then((res) => {
      if (!res.ok) throw new Error(`Failed to load search-index.json (${res.status})`);
      return res.json() as Promise<SearchIndexItem[]>;
    });
  }
  return searchIndexPromise;
}

export async function fetchCityData(citySlug: string): Promise<CityData> {
  const cached = cityCache.get(citySlug);
  if (cached) return cached;

  const res = await fetch(`/data/${encodeURIComponent(citySlug)}.json`);
  if (!res.ok) {
    throw new Error(`City not found: ${citySlug}`);
  }
  const data = (await res.json()) as CityData;
  cityCache.set(citySlug, data);
  return data;
}
