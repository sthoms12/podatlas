import fs from 'node:fs';
import path from 'node:path';
import type { CitiesGeoJSON, CityData } from '../types/podatlas';

const PUBLIC_DIR = path.resolve(process.cwd(), 'public');
const DATA_DIR = path.join(PUBLIC_DIR, 'data');

export function loadAllCitiesSync(): CityData[] {
  if (!fs.existsSync(DATA_DIR)) return [];
  const files = fs.readdirSync(DATA_DIR).filter((f) => f.endsWith('.json')).sort();
  return files.map((file) => {
    const raw = fs.readFileSync(path.join(DATA_DIR, file), 'utf-8');
    return JSON.parse(raw) as CityData;
  });
}

export function loadCityBySlugSync(slug: string): CityData | null {
  const filePath = path.join(DATA_DIR, `${slug}.json`);
  if (!fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as CityData;
}

export function loadCitiesGeoJsonSync(): CitiesGeoJSON {
  const filePath = path.join(PUBLIC_DIR, 'cities.geojson');
  if (!fs.existsSync(filePath)) {
    return { type: 'FeatureCollection', features: [] };
  }
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as CitiesGeoJSON;
}
