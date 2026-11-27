"""Offline unit tests for UNESCO World Heritage Open GeoJSON index, CLI hook, consensus, and KML."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from podatlas_pipeline.cli import merge_extraction_into_cities
from podatlas_pipeline.export_static import apply_city_consensus, build_city_kml
from podatlas_pipeline.geocode import GeoCity, GeoNamesIndex
from podatlas_pipeline.models import (
    CachedEpisodeTranscript,
    CityData,
    EpisodeExtraction,
    EpisodeRecord,
    ExtractedCityRef,
    ExtractedPlaceOrGear,
    PlaceOrGearRecord,
)
from podatlas_pipeline.unesco import UnescoIndex


def _sample_unesco_geojson() -> dict:
    kyoto_components = [
        {"name": "Kiyomizu-dera", "latitude": 34.99486, "longitude": 135.78505},
        {"name": "Nijō-jō", "latitude": 35.01423, "longitude": 135.74822},
        {"name": "Kinkaku-ji", "latitude": 35.03937, "longitude": 135.72924},
    ]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [
                        [135.78505, 34.99486],
                        [135.74822, 35.01423],
                    ],
                },
                "properties": {
                    "id_no": "688",
                    "name_en": "Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)",
                    "category": "Cultural",
                    "date_inscribed": "1994",
                    "iso_codes": ["JP"],
                    "geo_point_2d": {"lat": 35.0116, "lon": 135.7681},
                    "components_list": json.dumps(kyoto_components),
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [-72.54496, -13.16314],
                },
                "properties": {
                    "id_no": "274",
                    "name_en": "Historic Sanctuary of Machu Picchu",
                    "category": "Mixed",
                    "date_inscribed": "1983",
                    "iso_codes": "PE",
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [12.49223, 41.89021],
                },
                "properties": {
                    "id_no": "91",
                    "name_en": "Historic Centre of Rome",
                    "category": "Cultural",
                    "date_inscribed": "1980",
                    "iso_codes": "IT,VA",
                    "components_list": [
                        {"name": "Colosseum", "latitude": 41.89021, "longitude": 12.49223},
                    ],
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [2.3488, 48.8534],
                },
                "properties": {
                    "id_no": "600",
                    "name_en": "Paris, Banks of the Seine",
                    "category": "Cultural",
                    "date_inscribed": "1991",
                    "iso_codes": ["FR"],
                },
            },
        ],
    }


def test_parse_geojson_dict_and_file_with_multipoint_and_components(tmp_path: Path):
    geojson_path = tmp_path / "unesco_fixture.geojson"
    geojson_path.write_text(json.dumps(_sample_unesco_geojson()), encoding="utf-8")

    idx = UnescoIndex.from_geojson_file(geojson_path)
    # 4 top-level sites + 3 Kyoto components + 1 Rome component = 8 entries
    assert len(idx) == 8

    # Component match returns exact component coordinates and parent site metadata
    kiyomizu = idx.match("Kiyomizu-dera", 35.0116, 135.7681, country_code="JP")
    assert kiyomizu is not None
    assert kiyomizu.unesco_id == "688"
    assert kiyomizu.component_name == "Kiyomizu-dera"
    assert kiyomizu.lat == pytest.approx(34.99486, abs=1e-5)
    assert kiyomizu.lon == pytest.approx(135.78505, abs=1e-5)
    assert kiyomizu.iso_codes == ("JP",)

    # Diacritics + cross-language component matching ("Nijo Castle" -> "Nijō-jō")
    nijo = idx.match("Nijo Castle", 35.0116, 135.7681, country_code="JP")
    assert nijo is not None
    assert nijo.unesco_id == "688"
    assert nijo.component_name == "Nijō-jō"
    assert nijo.lat == pytest.approx(35.01423, abs=1e-5)
    assert nijo.lon == pytest.approx(135.74822, abs=1e-5)

    # Point geometry fallback without geo_point_2d
    machu = idx.match("Machu Picchu", -13.163, -72.545, country_code="PE")
    assert machu is not None
    assert machu.unesco_id == "274"
    assert machu.name == "Historic Sanctuary of Machu Picchu"
    assert machu.lat == pytest.approx(-13.16314, abs=1e-5)


def test_anti_false_positive_and_spatial_iso_filters():
    idx = UnescoIndex.from_geojson_dict(_sample_unesco_geojson())

    # False positives against umbrella city site names MUST be rejected
    assert idx.match("Kyoto Tower", 35.0116, 135.7681, country_code="JP") is None
    assert idx.match("Kyoto Ramen", 35.0116, 135.7681, country_code="JP") is None
    assert idx.match("Kyoto", 35.0116, 135.7681, country_code="JP") is None
    assert idx.match("Rome Coffee Shop", 41.9028, 12.4964, country_code="IT") is None
    assert idx.match("Paris Cafe", 48.8566, 2.3522, country_code="FR") is None

    # Distance pre-filter: Tokyo is >350km from Kyoto
    assert idx.match("Kiyomizu-dera", 35.6762, 139.6503, country_code="JP") is None

    # ISO country code filter
    assert idx.match("Kiyomizu-dera", 35.0116, 135.7681, country_code="US") is None

    # Multi-ISO comma-separated string ("IT,VA") allows both IT and VA
    assert idx.match("Colosseum", 41.9028, 12.4964, country_code="IT") is not None
    assert idx.match("Colosseum", 41.9028, 12.4964, country_code="VA") is not None


def test_pre_nominatim_short_circuit_in_merge_extraction():
    idx = UnescoIndex.from_geojson_dict(_sample_unesco_geojson())
    geonames = GeoNamesIndex(
        [GeoCity("kyoto-japan", "Kyoto", "Japan", "JP", 35.0116, 135.7681, 1465000, "1857910")]
    )

    geocoder_calls: list[str] = []

    class FakeGeocoder:
        def geocode(self, place_name, city_name, country, city_lat, city_lon):
            geocoder_calls.append(place_name)
            return (35.001, 135.771)

    cached_ep = CachedEpisodeTranscript(
        id="ep-ky-1",
        guid="guid-ky-1",
        showSlug="history-japan",
        showTitle="History of Japan Podcast",
        category="History",
        title="Kyoto Temples and Ramen",
        audioUrl="https://example.com/ep1.mp3",
        durationSec=2400,
        publishedAt="2026-10-01",
        transcriptSource="rss_tag",
        segments=[],
    )
    extraction = EpisodeExtraction(
        isDestinationFocused=True,
        primaryCities=[ExtractedCityRef(city="Kyoto", country="Japan")],
        summary="Exploring Kyoto.",
        placesAndGear=[
            ExtractedPlaceOrGear(
                name="Kiyomizu-dera",
                type="Landmark",
                note="Iconic wooden stage.",
                timestampSec=300,
            ),
            ExtractedPlaceOrGear(
                name="Kyoto Tower",
                type="Landmark",
                note="Modern observation tower.",
                timestampSec=600,
            ),
            ExtractedPlaceOrGear(
                name="Nishiki Ramen",
                type="Food",
                note="Rich broth.",
                timestampSec=900,
            ),
        ],
    )

    cities_by_id: dict[str, CityData] = {}
    updated = merge_extraction_into_cities(
        cities_by_id=cities_by_id,
        cached_ep=cached_ep,
        extraction=extraction,
        geonames=geonames,
        book_verifier=None,
        place_geocoder=FakeGeocoder(),  # type: ignore[arg-type]
        unesco_index=idx,
    )
    assert updated == 1
    places = {p.name: p for p in cities_by_id["kyoto-japan"].placesAndGear}

    # UNESCO landmark short-circuited Nominatim
    assert "Kiyomizu-dera" not in geocoder_calls
    assert places["Kiyomizu-dera"].isUnesco is True
    assert places["Kiyomizu-dera"].unescoId == "688"
    assert places["Kiyomizu-dera"].unescoName == "Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)"
    assert places["Kiyomizu-dera"].lat == pytest.approx(34.99486, abs=1e-5)
    assert places["Kiyomizu-dera"].lon == pytest.approx(135.78505, abs=1e-5)

    # Non-UNESCO landmark and food spot still fell through to Nominatim
    assert "Kyoto Tower" in geocoder_calls
    assert "Nishiki Ramen" in geocoder_calls
    assert places["Kyoto Tower"].isUnesco is False
    assert places["Kyoto Tower"].unescoId is None


def test_consensus_preserves_unesco_and_kml_formats_cleanly():
    city = CityData(
        id="kyoto-japan",
        name="Kyoto",
        country="Japan",
        lat=35.0116,
        lon=135.7681,
        primaryCategory="History",
        tagline="Former imperial capital.",
        episodes=[
            EpisodeRecord(
                id="ep-1",
                show="Show A",
                title="Ep 1",
                category="History",
                durationSec=1800,
                publishedAt="2026-09-01",
                summary="Summary 1",
            ),
            EpisodeRecord(
                id="ep-2",
                show="Show B",
                title="Ep 2",
                category="Travel",
                durationSec=2000,
                publishedAt="2026-09-02",
                summary="Summary 2",
            ),
        ],
        placesAndGear=[
            PlaceOrGearRecord(
                name="Kiyomizu-dera",
                type="Landmark",
                note="Great views from the wooden veranda.",
                episodeId="ep-1",
                timestampSec=125,
                show="Show A",
                lat=35.0000,
                lon=135.7000,
                isUnesco=False,
            ),
            PlaceOrGearRecord(
                name="Kiyomizu-dera",
                type="Landmark",
                note="Must visit at sunrise.",
                episodeId="ep-2",
                timestampSec=240,
                show="Show B",
                lat=34.99486,
                lon=135.78505,
                isUnesco=True,
                unescoId="688",
                unescoName="Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)",
            ),
            PlaceOrGearRecord(
                name="Standalone Park Overlook",
                type="Landmark",
                note="Official trailhead viewpoint.",
                episodeId="",
                timestampSec=0,
                show="",
                lat=35.0200,
                lon=135.7500,
                source="nps",
            ),
        ],
    )

    merged = apply_city_consensus(city)
    by_name = {p.name: p for p in merged.placesAndGear}
    k = by_name["Kiyomizu-dera"]
    assert k.isUnesco is True
    assert k.unescoId == "688"
    assert k.unescoName == "Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)"
    # UNESCO coordinates preferred over non-UNESCO coordinates
    assert k.lat == pytest.approx(34.99486, abs=1e-5)
    assert k.lon == pytest.approx(135.78505, abs=1e-5)
    assert k.uniqueShowCount == 2

    kml = build_city_kml(merged)
    assert "[Landmark] [UNESCO World Heritage] Recommended by 2 shows." in kml
    # Standalone place without episodeId or show must NOT emit (@ 00:00)
    assert "<description>[Landmark] Official trailhead viewpoint.</description>" in kml
    assert "(@ 00:00)" not in kml


def test_download_and_load_graceful_degradation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _failing_get(*args, **kwargs):
        raise httpx.ConnectError("offline test")

    monkeypatch.setattr(httpx.Client, "get", _failing_get)

    # Without cache -> returns empty index gracefully
    empty_idx = UnescoIndex.download_and_load(tmp_path / "no_cache")
    assert len(empty_idx) == 0

    # With stale cache -> falls back to stale cache gracefully
    stale_dir = tmp_path / "stale_cache"
    stale_dir.mkdir(parents=True)
    cache_file = stale_dir / "unesco_whc.geojson"
    cache_file.write_text(json.dumps(_sample_unesco_geojson()), encoding="utf-8")
    old_ts = 1_000_000_000
    os.utime(cache_file, (old_ts, old_ts))

    stale_idx = UnescoIndex.download_and_load(stale_dir, max_age_days=1.0)
    assert len(stale_idx) == 8
