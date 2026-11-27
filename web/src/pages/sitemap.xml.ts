import type { APIRoute } from 'astro';
import {
  loadAllCitiesSync,
  loadAllCountriesSync,
  loadAllShowsSync,
} from '../lib/staticData';

export const GET: APIRoute = ({ site }) => {
  const baseUrl = (site?.toString() || 'https://podatlas.app').replace(/\/+$/, '');
  const cities = loadAllCitiesSync();
  const countries = loadAllCountriesSync();
  const shows = loadAllShowsSync();

  const urls: Array<{ loc: string; priority: string }> = [
    { loc: `${baseUrl}/`, priority: '1.0' },
    ...countries.map((c) => ({
      loc: `${baseUrl}/country/${encodeURIComponent(c.slug)}`,
      priority: '0.9',
    })),
    ...cities.map((c) => ({
      loc: `${baseUrl}/city/${encodeURIComponent(c.id)}`,
      priority: '0.8',
    })),
    ...shows.map((s) => ({
      loc: `${baseUrl}/show/${encodeURIComponent(s.slug)}`,
      priority: '0.7',
    })),
  ];

  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls.map((u) => `  <url><loc>${u.loc}</loc><priority>${u.priority}</priority></url>`),
    '</urlset>',
    '',
  ].join('\n');

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
    },
  });
};
