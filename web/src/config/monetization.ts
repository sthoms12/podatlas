import type { BookRecord, PlaceOrGearRecord } from '../types/podatlas';

const env = import.meta.env ?? {};

export const MONETIZATION_CONFIG = {
  bookshopAffiliateId: (env.VITE_BOOKSHOP_AFFILIATE_ID as string | undefined)?.trim() || '',
  amazonAssociateTag: (env.VITE_AMAZON_ASSOCIATE_TAG as string | undefined)?.trim() || '',
  viatorPartnerId: (env.VITE_VIATOR_PARTNER_ID as string | undefined)?.trim() || '',
  stripeTripPackUrl: (env.VITE_STRIPE_TRIP_PACK_URL as string | undefined)?.trim() || '',
};

export function buildBookshopUrl(book: Pick<BookRecord, 'title' | 'author' | 'isbn'>): string {
  const affiliateParam = MONETIZATION_CONFIG.bookshopAffiliateId
    ? `&affiliate=${encodeURIComponent(MONETIZATION_CONFIG.bookshopAffiliateId)}`
    : '';
  if (book.isbn) {
    return `https://bookshop.org/search?keywords=${encodeURIComponent(book.isbn)}${affiliateParam}`;
  }
  const query = `${book.title} ${book.author}`.trim();
  return `https://bookshop.org/search?keywords=${encodeURIComponent(query)}${affiliateParam}`;
}

export function buildAmazonUrl(query: string): string {
  const tagParam = MONETIZATION_CONFIG.amazonAssociateTag
    ? `&tag=${encodeURIComponent(MONETIZATION_CONFIG.amazonAssociateTag)}`
    : '';
  return `https://www.amazon.com/s?k=${encodeURIComponent(query.trim())}${tagParam}`;
}

export function buildGoogleMapsUrl(place: Pick<PlaceOrGearRecord, 'name'>, cityName: string): string {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${place.name} ${cityName}`)}`;
}

export function buildViatorUrl(cityName: string, placeName?: string): string {
  const query = placeName ? `${placeName} ${cityName}` : cityName;
  const pidParam = MONETIZATION_CONFIG.viatorPartnerId
    ? `&pid=${encodeURIComponent(MONETIZATION_CONFIG.viatorPartnerId)}`
    : '';
  return `https://www.viator.com/searchResults/all?text=${encodeURIComponent(query)}${pidParam}`;
}
