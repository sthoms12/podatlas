import { useEffect, useMemo, useRef, useState } from 'react';
import Fuse from 'fuse.js';
import { Globe, Search, X } from 'lucide-react';
import type { CategoryFilter, DrawerTab, SearchIndexItem } from '../types/podatlas';

const CATEGORIES: CategoryFilter[] = ['All', 'Travel', 'History', 'Food', 'Culture'];

interface TopBarProps {
  activeCategory: CategoryFilter;
  onCategoryChange: (cat: CategoryFilter) => void;
  searchItems: SearchIndexItem[];
  onSelectSearchItem: (item: SearchIndexItem) => void;
}

export function TopBar({
  activeCategory,
  onCategoryChange,
  searchItems,
  onSelectSearchItem,
}: TopBarProps) {
  const [query, setQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const fuse = useMemo(
    () =>
      new Fuse(searchItems, {
        keys: [
          { name: 'title', weight: 0.65 },
          { name: 'subtitle', weight: 0.3 },
          { name: 'kind', weight: 0.05 },
        ],
        threshold: 0.35,
        ignoreLocation: true,
      }),
    [searchItems]
  );

  const results = useMemo(() => {
    const trimmed = query.trim();
    if (!trimmed) return [];
    return fuse.search(trimmed, { limit: 8 }).map((r) => r.item);
  }, [fuse, query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const kindBadgeColor = (tab: DrawerTab, kind: SearchIndexItem['kind']) => {
    if (kind === 'City') return 'bg-sky-500/15 text-sky-300 border-sky-500/30';
    if (tab === 'books') return 'bg-amber-500/15 text-amber-300 border-amber-500/30';
    if (tab === 'places') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
    return 'bg-slate-800 text-slate-300 border-slate-700';
  };

  return (
    <header className="shrink-0 border-b border-slate-800 bg-slate-900/95 backdrop-blur px-4 py-2.5 flex flex-wrap items-center justify-between gap-3 z-30">
      {/* Brand */}
      <div className="flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-sky-500 text-slate-950 flex items-center justify-center shadow-sm shadow-sky-500/20">
          <Globe className="w-4 h-4 stroke-[2.4]" />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="font-semibold text-sm text-slate-100 leading-tight tracking-tight">
              PodAtlas
            </h1>
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-slate-800 text-sky-300 border border-slate-700 font-medium">
              Beta
            </span>
          </div>
          <p className="text-[11px] text-slate-400 leading-tight">
            Podcast World Map · Books, Places &amp; Gear
          </p>
        </div>
      </div>

      {/* Fuse.js Cross-Entity Search */}
      <div ref={containerRef} className="relative flex-1 min-w-[220px] max-w-md">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
        <input
          type="text"
          value={query}
          onFocus={() => setIsOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setIsOpen(true);
          }}
          placeholder="Search city, book, podcast, or restaurant (e.g. Kyoto, SPQR, Tacos)..."
          className="w-full pl-9 pr-8 py-1.5 text-xs rounded-lg bg-slate-950 border border-slate-800 text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-sky-500 transition"
        />
        {query && (
          <button
            type="button"
            onClick={() => {
              setQuery('');
              setIsOpen(false);
            }}
            aria-label="Clear search"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-200"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}

        {isOpen && query.trim().length > 0 && (
          <div className="absolute left-0 right-0 top-full mt-1.5 rounded-xl bg-slate-900 border border-slate-700 shadow-2xl z-50 max-h-80 overflow-y-auto custom-scroll">
            {results.length === 0 ? (
              <div className="p-3 text-xs text-slate-400">
                No matches found for &ldquo;{query}&rdquo;
              </div>
            ) : (
              results.map((item, idx) => (
                <button
                  key={`${item.cityId}-${item.kind}-${item.title}-${idx}`}
                  type="button"
                  onClick={() => {
                    onSelectSearchItem(item);
                    setIsOpen(false);
                    setQuery('');
                  }}
                  className="w-full text-left px-3 py-2 hover:bg-slate-800/80 border-b border-slate-800 last:border-0 flex items-center justify-between gap-2 transition"
                >
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-slate-100 truncate">{item.title}</div>
                    <div className="text-[11px] text-slate-400 truncate">{item.subtitle}</div>
                  </div>
                  <span
                    className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border font-medium ${kindBadgeColor(
                      item.tab,
                      item.kind
                    )}`}
                  >
                    {item.kind}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {/* Category Filter Pills */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {CATEGORIES.map((cat) => {
          const active = cat === activeCategory;
          return (
            <button
              key={cat}
              type="button"
              onClick={() => onCategoryChange(cat)}
              className={`px-2.5 py-1 rounded-md text-xs font-medium transition cursor-pointer ${
                active
                  ? 'bg-sky-500 text-slate-950 shadow-sm'
                  : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
              }`}
            >
              {cat}
            </button>
          );
        })}
      </div>
    </header>
  );
}
