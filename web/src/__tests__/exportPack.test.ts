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
    const rss = buildCityRssFeed(SAMPLE_CITY, 'https://podatlas.app');
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

  it('emits [UNESCO World Heritage] in KML only for UNESCO landmarks', () => {
    const cityWithUnesco: CityData = {
      ...SAMPLE_CITY,
      placesAndGear: [
        {
          name: 'Kinkaku-ji',
          type: 'Landmark',
          note: 'Golden Pavilion reflecting in the pond.',
          episodeId: 'ky-1',
          timestampSec: 600,
          show: 'History of Japan Podcast',
          lat: 35.0394,
          lon: 135.7292,
          isUnesco: true,
          unescoId: '688',
          unescoName: 'Historic Monuments of Ancient Kyoto',
        },
        ...SAMPLE_CITY.placesAndGear,
      ],
    };

    const kml = buildCityKml(cityWithUnesco);
    expect(kml).toContain(
      '<description>[Landmark] [UNESCO World Heritage] Golden Pavilion reflecting in the pond. (History of Japan Podcast @ 10:00)</description>'
    );
    expect(kml).toContain(
      '<description>[Landmark] Recommended by 2 shows. Quiet mossy gate. (History of Japan Podcast @ 18:40)</description>'
    );
  });

  it('omits empty show and timestamp suffixes in KML for standalone NPS places', () => {
    const npsCity: CityData = {
      ...SAMPLE_CITY,
      placesAndGear: [
        {
          name: 'Yosemite Valley Overlook',
          type: 'Landmark',
          note: 'Granite cliffs and glacial valley.',
          episodeId: '',
          timestampSec: 0,
          show: '',
          lat: 37.7456,
          lon: -119.5936,
          source: 'nps',
          isUnesco: true,
          unescoId: '308',
          unescoName: 'Yosemite National Park',
        },
      ],
    };

    const kml = buildCityKml(npsCity);
    expect(kml).toContain(
      '<description>[Landmark] [UNESCO World Heritage] Granite cliffs and glacial valley.</description>'
    );
    expect(kml).not.toContain('(@ 00:00)');
    expect(kml).not.toContain('()');
  });

  it('formats UNESCO places, standalone NPS places, and Wikidata cold-start books cleanly in itinerary text', () => {
    const expandedCity: CityData = {
      ...SAMPLE_CITY,
      episodes: [
        ...SAMPLE_CITY.episodes,
        {
          id: 'nps-1',
          show: 'National Park Service',
          title: 'Ranger Walk Through the Cedar Grove',
          category: 'History',
          audioUrl: 'https://www.nps.gov/audio/cedar.mp3',
          durationSec: 420,
          publishedAt: '2026-08-01',
          summary: 'Public domain ranger tour.',
          mentions: [],
          source: 'nps',
        },
      ],
      books: [
        ...SAMPLE_CITY.books,
        {
          title: 'The Temple of the Golden Pavilion',
          author: 'Yukio Mishima',
          episodeId: '',
          show: '',
          timestampSec: 0,
          quote: '',
          wikidataId: 'Q1197296',
          source: 'wikidata',
        },
      ],
      placesAndGear: [
        {
          name: 'Kinkaku-ji',
          type: 'Landmark',
          note: 'Golden Pavilion.',
          episodeId: '',
          timestampSec: 0,
          show: '',
          lat: 35.0394,
          lon: 135.7292,
          isUnesco: true,
          unescoId: '688',
          source: 'nps',
        },
      ],
    };

    const txt = buildTripItineraryText(expandedCity);
    expect(txt).toContain(
      '- The Temple of the Golden Pavilion by Yukio Mishima [Set in Kyoto]'
    );
    expect(txt).toContain(
      '- [Landmark] [UNESCO World Heritage] Kinkaku-ji (35.0394, 135.7292): Golden Pavilion.'
    );
    expect(txt).toContain('Ranger Walk Through the Cedar Grove [NPS Audio]');
    expect(txt).not.toContain('(@ 00:00)');
    expect(txt).not.toContain('( @ 00:00)');
  });

  it('escapes XML special characters for UNESCO places in KML', () => {
    const specialCity: CityData = {
      ...SAMPLE_CITY,
      placesAndGear: [
        {
          name: 'St. Peter & Paul <Cathedral>',
          type: 'Landmark',
          note: 'Known for bells & mosaics > 500 years old.',
          episodeId: '',
          timestampSec: 0,
          show: '',
          lat: 41.9022,
          lon: 12.4539,
          isUnesco: true,
          unescoId: '91',
        },
      ],
    };

    const kml = buildCityKml(specialCity);
    expect(kml).toContain('<name>St. Peter &amp; Paul &lt;Cathedral&gt;</name>');
    expect(kml).toContain(
      '<description>[Landmark] [UNESCO World Heritage] Known for bells &amp; mosaics &gt; 500 years old.</description>'
    );
  });
});
