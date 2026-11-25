import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import type {
  ActivePlayback,
  CategoryFilter,
  CitiesGeoJSON,
  CityData,
  DrawerTab,
  EpisodeRecord,
  SearchIndexItem,
} from './types/podatlas';
import { fetchCitiesGeoJSON, fetchCityData, fetchSearchIndex } from './lib/data';
import { TopBar } from './components/TopBar';
import { WorldMap } from './components/WorldMap';
import { CityDrawer } from './components/CityDrawer';
import { AudioPlayer } from './components/AudioPlayer';
import { TripPackModal } from './components/TripPackModal';

const DEFAULT_CITY_SLUG = 'kyoto-japan';

function PodAtlasWorkspace() {
  const { slug } = useParams<{ slug?: string }>();
  const navigate = useNavigate();

  const [geoJson, setGeoJson] = useState<CitiesGeoJSON | null>(null);
  const [searchItems, setSearchItems] = useState<SearchIndexItem[]>([]);
  const [activeCategory, setActiveCategory] = useState<CategoryFilter>('All');
  const [activeTab, setActiveTab] = useState<DrawerTab>('episodes');

  const [cityData, setCityData] = useState<CityData | null>(null);
  const [cityLoading, setCityLoading] = useState(false);
  const [cityError, setCityError] = useState<string | null>(null);

  const [playback, setPlayback] = useState<ActivePlayback | null>(null);
  const [isTripPackOpen, setIsTripPackOpen] = useState(false);
  const requestIdRef = useRef(0);

  const selectedCityId = slug || DEFAULT_CITY_SLUG;

  // Load lightweight global geojson + search index once on startup
  useEffect(() => {
    fetchCitiesGeoJSON()
      .then(setGeoJson)
      .catch((err) => console.error('Failed to load cities.geojson', err));
    fetchSearchIndex()
      .then(setSearchItems)
      .catch((err) => console.error('Failed to load search-index.json', err));
  }, []);

  // Load selected city payload on demand
  useEffect(() => {
    let cancelled = false;
    setCityLoading(true);
    setCityError(null);

    fetchCityData(selectedCityId)
      .then((data) => {
        if (cancelled) return;
        setCityData(data);
        document.title = `${data.name}, ${data.country} — PodAtlas Podcast World Map`;
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setCityError(err.message);
      })
      .finally(() => {
        if (!cancelled) setCityLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedCityId]);

  const handleSelectCity = useCallback(
    (cityId: string) => {
      navigate(`/city/${encodeURIComponent(cityId)}`);
    },
    [navigate]
  );

  const handleCategoryChange = useCallback(
    (cat: CategoryFilter) => {
      setActiveCategory(cat);
      if (!geoJson || cat === 'All') return;
      const matches = geoJson.features.filter(
        (f) => f.properties.primaryCategory === cat || f.properties.categories.includes(cat)
      );
      if (matches.length > 0 && !matches.some((f) => f.properties.id === selectedCityId)) {
        navigate(`/city/${encodeURIComponent(matches[0].properties.id)}`);
      }
    },
    [geoJson, navigate, selectedCityId]
  );

  const handleSelectSearchItem = useCallback(
    (item: SearchIndexItem) => {
      setActiveCategory('All');
      setActiveTab(item.tab);
      navigate(`/city/${encodeURIComponent(item.cityId)}`);
    },
    [navigate]
  );

  const handlePlayEpisode = useCallback(
    (episode: EpisodeRecord, startSec: number, hintText = '') => {
      if (!cityData) return;
      requestIdRef.current += 1;
      setPlayback({
        cityId: cityData.id,
        cityName: cityData.name,
        country: cityData.country,
        episode,
        startSec,
        hintText,
        requestId: requestIdRef.current,
      });
    },
    [cityData]
  );

  const handleDefaultPlay = useCallback(() => {
    if (cityData && cityData.episodes.length > 0) {
      handlePlayEpisode(cityData.episodes[0], 0);
    }
  }, [cityData, handlePlayEpisode]);

  return (
    <div className="h-full flex flex-col bg-slate-950 text-slate-100">
      <TopBar
        activeCategory={activeCategory}
        onCategoryChange={handleCategoryChange}
        searchItems={searchItems}
        onSelectSearchItem={handleSelectSearchItem}
      />

      <div className="flex-1 flex flex-col sm:flex-row min-h-0 overflow-hidden">
        <WorldMap
          geoJson={geoJson}
          activeCategory={activeCategory}
          selectedCityId={selectedCityId}
          onSelectCity={handleSelectCity}
        />

        <CityDrawer
          city={cityData}
          loading={cityLoading}
          error={cityError}
          activeTab={activeTab}
          activeEpisodeId={playback?.episode.id ?? null}
          onTabChange={setActiveTab}
          onPlayEpisode={handlePlayEpisode}
          onOpenTripPack={() => setIsTripPackOpen(true)}
        />
      </div>

      <AudioPlayer playback={playback} onRequestDefaultPlay={handleDefaultPlay} />

      <TripPackModal
        city={cityData}
        isOpen={isTripPackOpen}
        onClose={() => setIsTripPackOpen(false)}
      />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<PodAtlasWorkspace />} />
        <Route path="/city/:slug" element={<PodAtlasWorkspace />} />
        <Route path="*" element={<PodAtlasWorkspace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
