export type Category = 'Travel' | 'History' | 'Food' | 'Culture';
export type CategoryFilter = 'All' | Category;
export type DrawerTab = 'episodes' | 'books' | 'places';
export type MentionType = 'book' | 'place' | 'gear';
export type PlaceType = 'Landmark' | 'Food' | 'Gear';
export type EndorsementLevel = 'strong_rec' | 'casual_mention';

export interface EpisodeMention {
  sec: number;
  type: MentionType;
  label: string;
}

export interface EpisodeRecord {
  id: string;
  show: string;
  title: string;
  category: Category;
  audioUrl: string;
  durationSec: number;
  publishedAt: string;
  summary: string;
  mentions: EpisodeMention[];
}

export interface BookRecord {
  title: string;
  author: string;
  isbn?: string | null;
  openLibraryKey?: string | null;
  episodeId: string;
  show: string;
  timestampSec: number;
  quote: string;
  endorsement?: EndorsementLevel;
  mentionCount?: number;
  uniqueShowCount?: number;
  mentionedByShows?: string[];
}

export interface PlaceOrGearRecord {
  name: string;
  type: PlaceType;
  note: string;
  episodeId: string;
  timestampSec: number;
  show?: string;
  lat?: number | null;
  lon?: number | null;
  endorsement?: EndorsementLevel;
  mentionCount?: number;
  uniqueShowCount?: number;
  mentionedByShows?: string[];
}

export interface CityData {
  id: string;
  name: string;
  country: string;
  lat: number;
  lon: number;
  primaryCategory: Category;
  tagline: string;
  episodes: EpisodeRecord[];
  books: BookRecord[];
  placesAndGear: PlaceOrGearRecord[];
}

export interface CityFeatureProperties {
  id: string;
  name: string;
  country: string;
  primaryCategory: Category;
  categories: Category[];
  episodeCount: number;
  bookCount: number;
  placeCount: number;
}

export interface CityGeoJSONFeature {
  type: 'Feature';
  geometry: {
    type: 'Point';
    coordinates: [number, number]; // [lon, lat]
  };
  properties: CityFeatureProperties;
}

export interface CitiesGeoJSON {
  type: 'FeatureCollection';
  features: CityGeoJSONFeature[];
}

export interface SearchIndexItem {
  kind: 'City' | 'Episode' | 'Book' | 'Landmark' | 'Food' | 'Gear';
  title: string;
  subtitle: string;
  cityId: string;
  tab: DrawerTab;
  episodeId?: string;
  timestampSec?: number;
}

export interface ActivePlayback {
  cityId: string;
  cityName: string;
  country: string;
  episode: EpisodeRecord;
  startSec: number;
  hintText: string;
  requestId: number;
}
