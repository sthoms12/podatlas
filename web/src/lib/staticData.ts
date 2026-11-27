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

export function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export interface CountryEpisodeItem {
  cityId: string;
  cityName: string;
  episode: CityData['episodes'][number];
}

export interface CountryBookItem {
  cityId: string;
  cityName: string;
  book: CityData['books'][number];
}

export interface CountryPlaceItem {
  cityId: string;
  cityName: string;
  place: CityData['placesAndGear'][number];
}

export interface CountrySummary {
  slug: string;
  name: string;
  cities: CityData[];
  episodes: CountryEpisodeItem[];
  books: CountryBookItem[];
  placesAndGear: CountryPlaceItem[];
  shows: string[];
}

export interface ShowSummary {
  slug: string;
  name: string;
  categories: string[];
  cities: Array<{ id: string; name: string; country: string }>;
  episodes: CountryEpisodeItem[];
  books: CountryBookItem[];
  placesAndGear: CountryPlaceItem[];
}

export function loadAllCountriesSync(): CountrySummary[] {
  const cities = loadAllCitiesSync();
  const bySlug = new Map<string, CountrySummary>();

  for (const city of cities) {
    const slug = slugify(city.country);
    if (!slug) continue;
    let entry = bySlug.get(slug);
    if (!entry) {
      entry = {
        slug,
        name: city.country,
        cities: [],
        episodes: [],
        books: [],
        placesAndGear: [],
        shows: [],
      };
      bySlug.set(slug, entry);
    }
    entry.cities.push(city);
    for (const episode of city.episodes) {
      entry.episodes.push({ cityId: city.id, cityName: city.name, episode });
    }
    for (const book of city.books) {
      entry.books.push({ cityId: city.id, cityName: city.name, book });
    }
    for (const place of city.placesAndGear) {
      entry.placesAndGear.push({ cityId: city.id, cityName: city.name, place });
    }
  }

  for (const entry of bySlug.values()) {
    const seenShows = new Set<string>();
    for (const item of entry.episodes) {
      seenShows.add(item.episode.show);
    }
    entry.shows = Array.from(seenShows).sort((a, b) => a.localeCompare(b));
  }

  return Array.from(bySlug.values()).sort((a, b) => a.name.localeCompare(b.name));
}

export function loadAllShowsSync(): ShowSummary[] {
  const cities = loadAllCitiesSync();
  const bySlug = new Map<
    string,
    {
      slug: string;
      name: string;
      categorySet: Set<string>;
      cityMap: Map<string, { id: string; name: string; country: string }>;
      episodes: CountryEpisodeItem[];
      books: CountryBookItem[];
      placesAndGear: CountryPlaceItem[];
    }
  >();

  const ensureShow = (showName: string) => {
    const slug = slugify(showName);
    if (!slug) return null;
    let entry = bySlug.get(slug);
    if (!entry) {
      entry = {
        slug,
        name: showName,
        categorySet: new Set<string>(),
        cityMap: new Map(),
        episodes: [],
        books: [],
        placesAndGear: [],
      };
      bySlug.set(slug, entry);
    }
    return entry;
  };

  for (const city of cities) {
    const epShowById = new Map<string, string>();
    for (const ep of city.episodes) {
      epShowById.set(ep.id, ep.show);
      const entry = ensureShow(ep.show);
      if (!entry) continue;
      entry.categorySet.add(ep.category);
      entry.cityMap.set(city.id, { id: city.id, name: city.name, country: city.country });
      entry.episodes.push({ cityId: city.id, cityName: city.name, episode: ep });
    }
    for (const book of city.books) {
      const canonicalShow = epShowById.get(book.episodeId) || book.show;
      const showNames = book.mentionedByShows?.length
        ? Array.from(new Set([canonicalShow, ...book.mentionedByShows]))
        : [canonicalShow];
      for (const showName of showNames) {
        const entry = bySlug.get(slugify(showName));
        if (!entry) continue;
        entry.cityMap.set(city.id, { id: city.id, name: city.name, country: city.country });
        entry.books.push({ cityId: city.id, cityName: city.name, book });
      }
    }
    for (const place of city.placesAndGear) {
      const canonicalShow = epShowById.get(place.episodeId) || place.show;
      const showNames = place.mentionedByShows?.length
        ? Array.from(new Set([...(canonicalShow ? [canonicalShow] : []), ...place.mentionedByShows]))
        : canonicalShow
          ? [canonicalShow]
          : [];
      for (const showName of showNames) {
        const entry = bySlug.get(slugify(showName));
        if (!entry) continue;
        entry.cityMap.set(city.id, { id: city.id, name: city.name, country: city.country });
        entry.placesAndGear.push({ cityId: city.id, cityName: city.name, place });
      }
    }
  }

  return Array.from(bySlug.values())
    .filter((entry) => entry.episodes.length > 0)
    .map((entry) => ({
      slug: entry.slug,
      name: entry.name,
      categories: Array.from(entry.categorySet).sort(),
      cities: Array.from(entry.cityMap.values()).sort((a, b) => a.name.localeCompare(b.name)),
      episodes: entry.episodes,
      books: entry.books,
      placesAndGear: entry.placesAndGear,
    }))
    .sort((a, b) => b.episodes.length - a.episodes.length || a.name.localeCompare(b.name));
}
