import { describe, expect, it } from 'vitest';
import type { CityData } from '../types/podatlas';
import { buildCityOpml, buildTripItineraryText } from '../lib/exportPack';

const SAMPLE_CITY: CityData = {
  id: 'kyoto-japan',
  name: 'Kyoto',
  country: 'Japan',
  lat: 35.01,
  lon: 135.77,
  primaryCategory: 'History',
  tagline: 'Zen gardens and Edo-era lore.',
  episodes: [
    {
      id: 'ky-1',
      show: 'History of Japan Podcast',
      title: 'Walking the Tokaido',
      category: 'History',
      audioUrl: '',
      durationSec: 2880,
      publishedAt: '2026-09-14',
      summary: 'Merchant life in Kyoto.',
      mentions: [],
    },
  ],
  books: [
    {
      title: 'Kyoto: A Cultural History',
      author: 'John Dougill',
      isbn: '9780195301380',
      episodeId: 'ky-1',
      show: 'History of Japan Podcast',
      timestampSec: 840,
      quote: 'Best primer.',
    },
  ],
  placesAndGear: [
    {
      name: 'Honen-in Temple',
      type: 'Landmark',
      note: 'Quiet mossy gate.',
      episodeId: 'ky-1',
      timestampSec: 1120,
    },
  ],
};

describe('exportPack generators', () => {
  it('builds a readable trip itinerary text file', () => {
    const txt = buildTripItineraryText(SAMPLE_CITY);
    expect(txt).toContain('PODATLAS TRIP PACK — KYOTO, JAPAN');
    expect(txt).toContain('Walking the Tokaido');
    expect(txt).toContain('Kyoto: A Cultural History by John Dougill');
    expect(txt).toContain('[Landmark] Honen-in Temple');
  });

  it('builds valid OPML 2.0 XML containing unique show names', () => {
    const opml = buildCityOpml(SAMPLE_CITY);
    expect(opml).toContain('<opml version="2.0">');
    expect(opml).toContain('PodAtlas Trip Playlist — Kyoto, Japan');
    expect(opml).toContain('History of Japan Podcast');
  });
});
