import { describe, expect, it } from 'vitest';
import {
  buildAmazonUrl,
  buildBookshopUrl,
  buildGoogleMapsUrl,
  buildViatorUrl,
} from '../config/monetization';

describe('monetization URL builders', () => {
  it('prefers ISBN when building Bookshop.org URLs', () => {
    const url = buildBookshopUrl({
      title: 'Kyoto: A Cultural History',
      author: 'John Dougill',
      isbn: '9780195301380',
    });
    expect(url).toContain('https://bookshop.org/search?keywords=9780195301380');
  });

  it('falls back to title + author when ISBN is absent', () => {
    const url = buildBookshopUrl({
      title: 'The Book of Tea',
      author: 'Kakuzo Okakura',
      isbn: null,
    });
    expect(url).toContain('The%20Book%20of%20Tea%20Kakuzo%20Okakura');
  });

  it('builds Amazon, Google Maps, and Viator URLs cleanly', () => {
    expect(buildAmazonUrl('Peak Design 30L')).toContain('amazon.com/s?k=Peak%20Design%2030L');
    expect(buildGoogleMapsUrl({ name: 'Honen-in Temple' }, 'Kyoto')).toContain(
      'Honen-in%20Temple%20Kyoto'
    );
    expect(buildViatorUrl('Kyoto', 'Gion')).toContain('viator.com/searchResults/all?text=Gion%20Kyoto');
  });
});
