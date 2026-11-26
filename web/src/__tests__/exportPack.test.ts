import { describe, expect, it } from 'vitest';
import type { CityData } from '../types/podatlas';
import {
  buildCityKml,
  buildCityOpml,
  buildCityRssFeed,
  buildTripItineraryText,
} from '../lib/exportPack';

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
      audioUrl: 'https://example.com/ky-1.mp3',
      durationSec: 2880,
      publishedAt: '2026-09-14',
      summary: 'Merchant life in Kyoto.',
      mentions: [{ sec: 840, type: 'book', label: 'Kyoto: A Cultural History' }],
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
      uniqueShowCount: 2,
      mentionedByShows: ['History of Japan Podcast', 'Zero to Travel'],
    },
  ],
  placesAndGear: [
    {
      name: 'Honen-in Temple',
      type: 'Landmark',
      note: 'Quiet mossy gate.',
      episodeId: 'ky-1',
      timestampSec: 1120,
      show: 'History of Japan Podcast',
      lat: 35.0221,
      lon: 135.7973,
      uniqueShowCount: 2,
    },
    {
      name: 'Peak Design 30L',
      type: 'Gear',
      note: 'Fits coin lockers.',
      episodeId: 'ky-1',
      timestampSec: 1500,
    },
  ],
};

describe('exportPack generators', () => {
  it('builds a readable trip itinerary text file with consensus annotations', () => {
    const txt = buildTripItineraryText(SAMPLE_CITY);
    expect(txt).toContain('PODATLAS TRIP PACK — KYOTO, JAPAN');
    expect(txt).toContain('Walking the Tokaido');
    expect(txt).toContain('Kyoto: A Cultural History by John Dougill [★ 2 shows agree]');
    expect(txt).toContain('[Landmark] Honen-in Temple [★ 2 shows]');
  });

  it('builds a valid playable Podcast RSS 2.0 feed with enclosures', () => {
    const rss = buildCityRssFeed(SAMPLE_CITY, 'https://podatlas.pages.dev');
    expect(rss).toContain('<rss version="2.0"');
    expect(rss).toContain('PodAtlas: Kyoto, Japan Podcast Playlist');
    expect(rss).toContain('<enclosure url="https://example.com/ky-1.mp3" length="0" type="audio/mpeg" />');
    expect(rss).toContain('History of Japan Podcast — Walking the Tokaido');
  });

  it('builds valid KML 2.2 with geocoded landmarks and omits gear', () => {
    const kml = buildCityKml(SAMPLE_CITY);
    expect(kml).toContain('<kml xmlns="http://www.opengis.net/kml/2.2">');
    expect(kml).toContain('<name>Honen-in Temple</name>');
    expect(kml).toContain('<coordinates>135.7973,35.0221,0</coordinates>');
    expect(kml).toContain('Recommended by 2 shows.');
    expect(kml).not.toContain('Peak Design 30L');
  });

  it('builds valid OPML 2.0 XML containing unique show names', () => {
    const opml = buildCityOpml(SAMPLE_CITY);
    expect(opml).toContain('<opml version="2.0">');
    expect(opml).toContain('PodAtlas Trip Playlist — Kyoto, Japan');
    expect(opml).toContain('History of Japan Podcast');
  });
});
