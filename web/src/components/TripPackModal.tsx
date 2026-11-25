import { Check, Download, ExternalLink, X } from 'lucide-react';
import type { CityData } from '../types/podatlas';
import { MONETIZATION_CONFIG } from '../config/monetization';
import {
  buildCityOpml,
  buildTripItineraryText,
  triggerFileDownload,
} from '../lib/exportPack';

interface TripPackModalProps {
  city: CityData | null;
  isOpen: boolean;
  onClose: () => void;
}

export function TripPackModal({ city, isOpen, onClose }: TripPackModalProps) {
  if (!isOpen || !city) return null;

  const handleDownloadTxt = () => {
    triggerFileDownload(
      `${city.id}-podatlas-trip-pack.txt`,
      buildTripItineraryText(city),
      'text/plain;charset=utf-8'
    );
  };

  const handleDownloadOpml = () => {
    triggerFileDownload(
      `${city.id}-podatlas-playlist.opml`,
      buildCityOpml(city),
      'text/x-opml;charset=utf-8'
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/65 backdrop-blur-xs flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-2xl max-w-md w-full p-5 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <span className="text-[11px] font-medium px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/30">
              Offline Flight Bundle
            </span>
            <h3 className="text-base font-semibold text-slate-100 mt-1.5">
              {city.name} Trip Audio &amp; Reading Pack
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close modal"
            className="text-slate-400 hover:text-slate-100 cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-xs text-slate-300 leading-relaxed">
          One-time <strong className="text-slate-100">$7</strong> export for travelers prepping for
          a flight — generated cleanly from PodAtlas&apos;s static city index with zero server cost:
        </p>

        <ul className="space-y-2 text-xs text-slate-200">
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>
              <strong>{city.episodes.length} curated podcast episodes</strong> queued as an offline
              OPML playlist
            </span>
          </li>
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>
              <strong>{city.books.length} pre-trip books</strong> with exact episode quotes &amp;
              timestamps
            </span>
          </li>
          <li className="flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-400 shrink-0" />
            <span>
              <strong>{city.placesAndGear.length} pinned restaurants, landmarks &amp; gear</strong>{' '}
              ready for Google Maps
            </span>
          </li>
        </ul>

        <div className="flex flex-col gap-2 pt-1">
          {MONETIZATION_CONFIG.stripeTripPackUrl && (
            <a
              href={MONETIZATION_CONFIG.stripeTripPackUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="w-full py-2 px-3 rounded-lg text-xs font-semibold bg-sky-500 text-slate-950 hover:bg-sky-400 transition flex items-center justify-center gap-1.5"
            >
              <span>Unlock Full $7 Trip Pack</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={handleDownloadTxt}
              className="py-2 px-3 rounded-lg text-xs font-medium bg-slate-800 text-slate-100 hover:bg-slate-700 transition flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Sample Itinerary (.txt)</span>
            </button>
            <button
              type="button"
              onClick={handleDownloadOpml}
              className="py-2 px-3 rounded-lg text-xs font-medium bg-slate-800 text-slate-100 hover:bg-slate-700 transition flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Podcast OPML (.opml)</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
