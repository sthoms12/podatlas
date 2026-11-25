import { BookOpen, Download, ExternalLink, MapPin, Play, Sparkles } from 'lucide-react';
import type { CityData, DrawerTab, EpisodeRecord } from '../types/podatlas';
import {
  buildAmazonUrl,
  buildBookshopUrl,
  buildGoogleMapsUrl,
  buildViatorUrl,
} from '../config/monetization';
import { formatCoords, formatTime } from '../lib/format';

interface CityDrawerProps {
  city: CityData | null;
  loading: boolean;
  error: string | null;
  activeTab: DrawerTab;
  activeEpisodeId: string | null;
  onTabChange: (tab: DrawerTab) => void;
  onPlayEpisode: (episode: EpisodeRecord, startSec: number, hintText?: string) => void;
  onOpenTripPack: () => void;
}

export function CityDrawer({
  city,
  loading,
  error,
  activeTab,
  activeEpisodeId,
  onTabChange,
  onPlayEpisode,
  onOpenTripPack,
}: CityDrawerProps) {
  const findEpisode = (epId: string): EpisodeRecord | undefined =>
    city?.episodes.find((ep) => ep.id === epId) || city?.episodes[0];

  if (loading && !city) {
    return (
      <aside className="w-full sm:w-[400px] lg:w-[440px] shrink-0 border-l border-slate-800 bg-slate-900 p-6 flex items-center justify-center text-xs text-slate-400">
        Loading destination index...
      </aside>
    );
  }

  if (error || !city) {
    return (
      <aside className="w-full sm:w-[400px] lg:w-[440px] shrink-0 border-l border-slate-800 bg-slate-900 p-6 flex flex-col items-center justify-center text-center gap-2">
        <p className="text-sm font-medium text-slate-200">Select a city on the map</p>
        <p className="text-xs text-slate-400">
          {error || 'Click any glowing city pin to explore curated episodes, books, and local places.'}
        </p>
      </aside>
    );
  }

  return (
    <aside className="w-full sm:w-[400px] lg:w-[440px] shrink-0 border-l border-slate-800 bg-slate-900 flex flex-col min-h-0">
      {/* City Header */}
      <div className="p-4 border-b border-slate-800 shrink-0">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-baseline gap-2">
              <h2 className="text-base font-semibold text-slate-100">{city.name}</h2>
              <span className="text-xs text-slate-400">{city.country}</span>
            </div>
            <p className="text-[11px] text-slate-400 mt-0.5 font-mono">
              {formatCoords(city.lat, city.lon)} · {city.episodes.length} episodes indexed
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenTripPack}
            className="shrink-0 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-slate-950 hover:bg-sky-400 transition flex items-center gap-1.5 cursor-pointer shadow-sm"
          >
            <Download className="w-3.5 h-3.5" />
            <span>$7 Trip Pack</span>
          </button>
        </div>

        <p className="text-xs text-slate-300 mt-2 leading-relaxed">{city.tagline}</p>

        {/* Drawer Tabs */}
        <div className="grid grid-cols-3 gap-1 mt-3 p-1 rounded-lg bg-slate-950 border border-slate-800">
          {(
            [
              { id: 'episodes', label: 'Episodes', count: city.episodes.length },
              { id: 'books', label: 'Books', count: city.books.length },
              { id: 'places', label: 'Places & Gear', count: city.placesAndGear.length },
            ] as const
          ).map((t) => {
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => onTabChange(t.id)}
                className={`py-1.5 rounded-md text-xs font-medium transition cursor-pointer ${
                  active
                    ? 'bg-slate-800 text-slate-100 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {t.label} <span className="text-slate-400 ml-0.5">({t.count})</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Drawer Content */}
      <div className="flex-1 overflow-y-auto custom-scroll p-4 space-y-3">
        {activeTab === 'episodes' &&
          city.episodes.map((ep) => {
            const isPlaying = activeEpisodeId === ep.id;
            return (
              <article
                key={ep.id}
                className={`p-3.5 rounded-xl bg-slate-950/90 border transition space-y-2.5 ${
                  isPlaying ? 'border-sky-500/80 ring-1 ring-sky-500/20' : 'border-slate-800'
                }`}
              >
                <div className="flex items-start justify-between gap-2.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-xs font-medium text-sky-400">{ep.show}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                        {ep.category}
                      </span>
                    </div>
                    <h3 className="text-xs font-semibold text-slate-100 mt-1 leading-snug">
                      {ep.title}
                    </h3>
                  </div>
                  <button
                    type="button"
                    onClick={() => onPlayEpisode(ep, 0)}
                    title="Play episode"
                    className="shrink-0 w-8 h-8 rounded-full bg-sky-500 text-slate-950 flex items-center justify-center hover:bg-sky-400 transition cursor-pointer"
                  >
                    <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                  </button>
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">{ep.summary}</p>

                <div className="flex items-center justify-between text-[11px] text-slate-500">
                  <span>{ep.publishedAt}</span>
                  <span className="font-mono">{formatTime(ep.durationSec)}</span>
                </div>

                {ep.mentions.length > 0 && (
                  <div className="pt-2 border-t border-slate-800/80 space-y-1.5">
                    <div className="text-[11px] font-medium text-slate-400">
                      Extracted Mentions (Jump to Timestamp):
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {ep.mentions.map((m, idx) => (
                        <button
                          key={`${ep.id}-m-${idx}`}
                          type="button"
                          onClick={() =>
                            onPlayEpisode(ep, m.sec, `${m.type.toUpperCase()}: ${m.label}`)
                          }
                          className="text-[11px] px-2 py-1 rounded-md bg-slate-900 border border-slate-800 hover:border-sky-500 text-slate-200 transition text-left cursor-pointer"
                        >
                          <span className="font-mono text-sky-400 mr-1.5">{formatTime(m.sec)}</span>
                          {m.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </article>
            );
          })}

        {activeTab === 'books' &&
          city.books.map((book, idx) => {
            const ep = findEpisode(book.episodeId);
            return (
              <article
                key={`${book.title}-${idx}`}
                className="p-3.5 rounded-xl bg-slate-950/90 border border-slate-800 space-y-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-xs font-semibold text-slate-100 leading-snug flex items-center gap-1.5">
                      <BookOpen className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                      <span>{book.title}</span>
                    </h3>
                    <p className="text-xs text-slate-400 mt-0.5">by {book.author}</p>
                  </div>
                  {ep && (
                    <button
                      type="button"
                      onClick={() =>
                        onPlayEpisode(ep, book.timestampSec, `Book: ${book.title}`)
                      }
                      className="shrink-0 text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-sky-400 hover:bg-slate-700 cursor-pointer"
                    >
                      ▶ {formatTime(book.timestampSec)}
                    </button>
                  )}
                </div>

                {book.quote && (
                  <p className="text-xs text-slate-300 italic leading-relaxed">
                    &ldquo;{book.quote}&rdquo;
                  </p>
                )}

                <div className="text-[11px] text-slate-400">
                  Mentioned on <span className="text-slate-200 font-medium">{book.show}</span>
                </div>

                <div className="flex items-center gap-2 pt-1.5">
                  <a
                    href={buildBookshopUrl(book)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-1 text-center py-1.5 rounded-lg text-xs font-medium bg-sky-500 text-slate-950 hover:bg-sky-400 transition"
                  >
                    Bookshop.org
                  </a>
                  <a
                    href={buildAmazonUrl(`${book.title} ${book.author}`)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-1 text-center py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-200 hover:bg-slate-700 transition"
                  >
                    Amazon
                  </a>
                </div>
              </article>
            );
          })}

        {activeTab === 'places' &&
          city.placesAndGear.map((item, idx) => {
            const ep = findEpisode(item.episodeId);
            const isGear = item.type === 'Gear';
            const badgeStyle = isGear
              ? 'bg-sky-500/15 text-sky-300 border-sky-500/30'
              : item.type === 'Food'
                ? 'bg-rose-500/15 text-rose-300 border-rose-500/30'
                : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';

            return (
              <article
                key={`${item.name}-${idx}`}
                className="p-3.5 rounded-xl bg-slate-950/90 border border-slate-800 space-y-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-xs font-semibold text-slate-100">{item.name}</h3>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${badgeStyle}`}>
                      {item.type}
                    </span>
                  </div>
                  {ep && (
                    <button
                      type="button"
                      onClick={() =>
                        onPlayEpisode(ep, item.timestampSec, `${item.type}: ${item.name}`)
                      }
                      className="shrink-0 text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-sky-400 hover:bg-slate-700 cursor-pointer"
                    >
                      ▶ {formatTime(item.timestampSec)}
                    </button>
                  )}
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">{item.note}</p>

                <div className="pt-1 flex items-center gap-3 flex-wrap">
                  {isGear ? (
                    <a
                      href={buildAmazonUrl(item.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs font-medium text-sky-400 hover:underline"
                    >
                      <span>View Gear on Amazon</span>
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  ) : (
                    <>
                      <a
                        href={buildGoogleMapsUrl(item, city.name)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs font-medium text-sky-400 hover:underline"
                      >
                        <MapPin className="w-3 h-3" />
                        <span>Open in Maps</span>
                      </a>
                      <a
                        href={buildViatorUrl(city.name, item.name)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-slate-200 hover:underline"
                      >
                        <Sparkles className="w-3 h-3" />
                        <span>Tours nearby</span>
                      </a>
                    </>
                  )}
                </div>
              </article>
            );
          })}
      </div>
    </aside>
  );
}
