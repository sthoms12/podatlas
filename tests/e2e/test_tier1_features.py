"""Tier 1 E2E Tests: Core Feature Contracts across Areas A–E (F1–F12)."""

from __future__ import annotations

import csv
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest

from podatlas_pipeline.cli import merge_extraction_into_cities, validate_output
from podatlas_pipeline.config import PipelineSettings
from podatlas_pipeline.export_static import apply_city_consensus, build_city_kml
from podatlas_pipeline.geocode import GeoNamesIndex, PlaceGeocoder
from podatlas_pipeline.models import (
    BookRecord,
    CachedEpisodeTranscript,
    CityData,
    EpisodeExtraction,
    EpisodeRecord,
    ExtractedCityRef,
    ExtractedPlaceOrGear,
    PlaceOrGearRecord,
)
from podatlas_pipeline.open_datasets import normalize_feed_url
from podatlas_pipeline.podcast_index import (
    ShowConfig,
    check_show_feed,
    fetch_rss_episodes,
    load_shows_csv,
)
from podatlas_pipeline.verify_books import OpenLibraryVerifier, VerifiedBook

from conftest import (
    LONG_SUBSTACK_ESSAY_HTML,
    SHOWS_CSV,
    WEB_DIR,
    WEB_PUBLIC_DIR,
    LocalMockServer,
    add_wikidata_sparql_route,
    call_nps_park_builder,
    call_nps_parks,
    get_wikidata_city_books_fn,
    import_planned_module,
    make_mixed_city_dict,
    make_nps_audio_payload,
    make_nps_client,
    make_nps_parks_payload,
    make_nps_places_payload,
    make_sparql_binding,
    make_substack_rss_xml,
    make_wikidata_client,
    run_cli,
    write_geonames_fixture,
    write_unesco_fixture,
)


# ===========================================================================
# AREA A: Substack <content:encoded> Fallback (F1, F2, F3)
# ===========================================================================


def test_area_a_fetch_rss_captures_content_encoded(mock_http_server: LocalMockServer) -> None:
    """F1: fetch_rss_episodes captures <content:encoded> HTML on RawEpisode.content_encoded."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/substack.xml", rss_xml, content_type="application/rss+xml")

    show = ShowConfig(
        slug="slow-kyoto",
        title="Slow Kyoto Dispatch",
        category="History",
        rss_url=f"{mock_http_server.base_url}/substack.xml",
        max_episodes=5,
    )
    episodes = fetch_rss_episodes(show)
    assert len(episodes) == 1
    ep = episodes[0]
    assert hasattr(ep, "content_encoded"), "RawEpisode must expose content_encoded attribute (F1)"
    assert "Kinkaku-ji" in ep.content_encoded
    assert "Yukio Mishima" in ep.content_encoded


def test_area_a_audio_enclosure_matches_m4a_with_query_params(
    mock_http_server: LocalMockServer,
) -> None:
    """F1: Audio enclosure matching accepts .m4a URLs carrying query string parameters."""
    rss_xml = make_substack_rss_xml(
        mock_http_server.base_url,
        audio_path="/media/episode-42.m4a?token=abc123&utm_source=substack",
        audio_mime="application/octet-stream",
    )
    mock_http_server.add_route("/m4a.xml", rss_xml, content_type="application/rss+xml")

    show = ShowConfig(
        slug="m4a-show",
        title="M4A Substack Show",
        category="Travel",
        rss_url=f"{mock_http_server.base_url}/m4a.xml",
        max_episodes=5,
    )
    episodes = fetch_rss_episodes(show)
    assert len(episodes) == 1
    assert ".m4a" in episodes[0].audio_url
    assert "token=abc123" in episodes[0].audio_url


def test_area_a_html_to_segments_strips_script_style_and_unescapes(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """F2: HTML-to-segments strips <script>/<style>, unescapes entities, and emits segments."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/essay.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"slow-kyoto,Slow Kyoto Dispatch,History,{mock_http_server.base_url}/essay.xml,,5\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    result = run_cli(
        ["transcribe", "--max-episodes", "1"],
        env_overrides={
            "PODATLAS_SHOWS_CSV": str(shows_csv),
            "PODATLAS_CACHE_DIR": str(cache_dir),
            "PODATLAS_OUTPUT_DIR": str(tmp_path / "out"),
            "PODATLAS_MIN_DURATION_SEC": "60",
        },
    )
    assert result.returncode == 0, f"transcribe failed: {result.stderr}"

    cached_files = list((cache_dir / "transcripts").glob("*.json"))
    assert len(cached_files) == 1, f"Expected 1 cached transcript, found {cached_files}"
    payload = json.loads(cached_files[0].read_text(encoding="utf-8"))
    combined_text = " ".join(seg["text"] for seg in payload.get("segments", []))
    assert "console.log" not in combined_text
    assert ".paywall" not in combined_text
    assert "Kyoto & its historic wooden temples" in combined_text
    assert "Kinkaku-ji" in combined_text


def test_area_a_cached_transcript_accepts_content_encoded_source() -> None:
    """F3: CachedEpisodeTranscript.transcriptSource accepts 'content_encoded'."""
    item = CachedEpisodeTranscript(
        id="ep-sub-1",
        guid="guid-sub-1",
        showSlug="slow-kyoto",
        showTitle="Slow Kyoto Dispatch",
        category="History",
        title="Kyoto Essay",
        audioUrl="https://example.org/ep1.m4a",
        durationSec=2400,
        publishedAt="2026-02-10",
        transcriptSource="content_encoded",
        segments=[],
    )
    assert item.transcriptSource == "content_encoded"


def test_area_a_transcribe_cli_uses_content_encoded_without_whisper(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """F3: podatlas-pipeline transcribe uses <content:encoded> fallback and skips faster-whisper."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/substack.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"slow-kyoto,Slow Kyoto Dispatch,History,{mock_http_server.base_url}/substack.xml,,5\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    proc = run_cli(
        ["transcribe", "--max-episodes", "1"],
        env_overrides={
            "PODATLAS_SHOWS_CSV": str(shows_csv),
            "PODATLAS_CACHE_DIR": str(cache_dir),
            "PODATLAS_OUTPUT_DIR": str(tmp_path / "out"),
            "PODATLAS_MIN_DURATION_SEC": "60",
        },
    )
    assert proc.returncode == 0, f"CLI failed (stdout={proc.stdout}, stderr={proc.stderr})"

    cached_files = list((cache_dir / "transcripts").glob("*.json"))
    assert len(cached_files) == 1
    cached = CachedEpisodeTranscript.model_validate_json(cached_files[0].read_text(encoding="utf-8"))
    assert cached.transcriptSource == "content_encoded"
    assert len(cached.segments) >= 1


def test_area_a_check_show_feed_reports_with_content_encoded(
    mock_http_server: LocalMockServer,
) -> None:
    """F3: check_show_feed reports withContentEncoded count for feeds with essay HTML."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/check.xml", rss_xml, content_type="application/rss+xml")

    show = ShowConfig(
        slug="slow-kyoto",
        title="Slow Kyoto Dispatch",
        category="History",
        rss_url=f"{mock_http_server.base_url}/check.xml",
        max_episodes=5,
    )
    report = check_show_feed(show)
    assert report["ok"] is True
    assert "withContentEncoded" in report, f"Missing withContentEncoded in {report}"
    assert report["withContentEncoded"] >= 1


# ===========================================================================
# AREA B: UNESCO World Heritage GeoJSON & KML/UI Contracts (F4, F5, F6, F10)
# ===========================================================================


def test_area_b_place_record_supports_unesco_fields() -> None:
    """F4: PlaceOrGearRecord exposes additive UNESCO fields with backward-compatible defaults."""
    default_place = PlaceOrGearRecord(
        name="Nishiki Market",
        type="Food",
        note="Historic market",
        episodeId="ep-1",
        timestampSec=120,
    )
    assert getattr(default_place, "isUnesco", None) is False
    assert getattr(default_place, "unescoId", "MISSING") is None
    assert getattr(default_place, "unescoName", "MISSING") is None
    assert getattr(default_place, "source", None) == "podcast"

    unesco_place = PlaceOrGearRecord(
        name="Kinkaku-ji",
        type="Landmark",
        note="Golden Pavilion",
        episodeId="ep-1",
        timestampSec=300,
        isUnesco=True,
        unescoId="688",
        unescoName="Historic Monuments of Ancient Kyoto",
    )
    assert unesco_place.isUnesco is True
    assert unesco_place.unescoId == "688"
    assert unesco_place.unescoName == "Historic Monuments of Ancient Kyoto"


def _load_unesco_index_from_fixture(tmp_path: Path) -> Any:
    unesco_mod = import_planned_module("podatlas_pipeline.unesco")
    UnescoIndex = getattr(unesco_mod, "UnescoIndex")
    fixture_path = write_unesco_fixture(tmp_path)
    for loader_name in ("from_geojson_file", "from_file", "load_from_file"):
        loader = getattr(UnescoIndex, loader_name, None)
        if callable(loader):
            return loader(fixture_path)
    if callable(getattr(UnescoIndex, "from_geojson", None)):
        return UnescoIndex.from_geojson(json.loads(fixture_path.read_text(encoding="utf-8")))
    return UnescoIndex(fixture_path)


def test_area_b_unesco_index_loads_geojson_and_components(tmp_path: Path) -> None:
    """F5: UnescoIndex loads whc001 GeoJSON including geo_point_2d and components_list."""
    idx = _load_unesco_index_from_fixture(tmp_path)
    match = idx.match("Kiyomizu-dera", 35.0211, 135.7539)
    assert match is not None, "Expected component 'Kiyomizu-dera' from components_list to match"
    assert abs(match.lat - 34.9949) < 0.05
    assert abs(match.lon - 135.7850) < 0.05


def test_area_b_unesco_index_spatial_and_name_match_returns_exact_coords(tmp_path: Path) -> None:
    """F5: UnescoIndex.match returns exact UNESCO coordinates and site metadata."""
    idx = _load_unesco_index_from_fixture(tmp_path)
    site = idx.match("Kinkaku-ji", 35.0211, 135.7539)
    assert site is not None
    assert str(getattr(site, "unesco_id", getattr(site, "id_no", ""))) == "688"
    assert abs(site.lat - 35.0394) < 0.01
    assert abs(site.lon - 135.7292) < 0.01


def test_area_b_merge_extraction_prefers_unesco_over_nominatim(tmp_path: Path) -> None:
    """F6: merge_extraction_into_cities checks UnescoIndex before Nominatim & --skip-unesco exists."""
    sig = inspect.signature(merge_extraction_into_cities)
    assert "unesco_index" in sig.parameters, (
        "merge_extraction_into_cities must accept unesco_index parameter (F6)"
    )

    idx = _load_unesco_index_from_fixture(tmp_path)
    geonames = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))

    class _FailIfCalledGeocoder(PlaceGeocoder):
        def geocode(self, *args: Any, **kwargs: Any) -> tuple[float, float] | None:
            raise AssertionError("Nominatim geocoder should NOT be called for a UNESCO match")

    cached_ep = CachedEpisodeTranscript(
        id="ep-kyoto-1",
        guid="g1",
        showSlug="slow-kyoto",
        showTitle="Slow Kyoto Dispatch",
        category="History",
        title="Kyoto Temples",
        audioUrl="https://example.org/ep1.m4a",
        durationSec=1800,
        publishedAt="2026-02-10",
        transcriptSource="content_encoded",
        segments=[],
    )
    extraction = EpisodeExtraction(
        isDestinationFocused=True,
        primaryCities=[ExtractedCityRef(city="Kyoto", country="Japan", tagline="Historic capital")],
        summary="Tour of Kinkaku-ji.",
        placesAndGear=[
            ExtractedPlaceOrGear(
                name="Kinkaku-ji",
                type="Landmark",
                note="Golden Pavilion",
                timestampSec=300,
                endorsement="strong_rec",
            )
        ],
    )
    cities: dict[str, CityData] = {}
    merge_extraction_into_cities(
        cities,
        cached_ep,
        extraction,
        geonames,
        place_geocoder=_FailIfCalledGeocoder(),
        unesco_index=idx,
    )
    assert "kyoto-japan" in cities
    place = cities["kyoto-japan"].placesAndGear[0]
    assert place.isUnesco is True
    assert place.unescoId == "688"
    assert place.lat is not None and abs(place.lat - 35.0394) < 0.01

    help_proc = run_cli(["extract", "--help"])
    assert help_proc.returncode == 0
    assert "--skip-unesco" in help_proc.stdout


def test_area_b_consensus_and_python_kml_emit_unesco_marker() -> None:
    """F6: apply_city_consensus preserves UNESCO fields even when a longer-note duplicate lacks them."""
    city = CityData.model_validate(make_mixed_city_dict())
    # Replace Kinkaku-ji with two duplicates: one short-note UNESCO-flagged, one longer-note non-UNESCO.
    # Naive longer-note winner selection would drop isUnesco/unescoId/unescoName unless explicitly preserved.
    city.placesAndGear = [p for p in city.placesAndGear if p.name != "Kinkaku-ji"]
    city.placesAndGear.extend(
        [
            PlaceOrGearRecord(
                name="Kinkaku-ji",
                type="Landmark",
                note="Golden Pavilion",
                episodeId="ep-kyoto-1",
                timestampSec=600,
                show="Slow Kyoto Dispatch",
                lat=35.0394,
                lon=135.7292,
                endorsement="casual_mention",
                isUnesco=True,
                unescoId="688",
                unescoName="Historic Monuments of Ancient Kyoto",
                source="podcast",
            ),
            PlaceOrGearRecord(
                name="Kinkaku-ji",
                type="Landmark",
                note="Famous gold-leaf Zen temple in northern Kyoto with pond garden and serene reflections.",
                episodeId="ep-kyoto-2",
                timestampSec=900,
                show="Japan Station",
                lat=35.0394,
                lon=135.7292,
                endorsement="strong_rec",
                isUnesco=False,
                unescoId=None,
                unescoName=None,
                source="podcast",
            ),
        ]
    )

    merged = apply_city_consensus(city)
    kinkaku_matches = [p for p in merged.placesAndGear if p.name == "Kinkaku-ji"]
    assert len(kinkaku_matches) == 1, f"Expected deduplicated Kinkaku-ji, found {len(kinkaku_matches)}"
    kinkaku = kinkaku_matches[0]
    assert kinkaku.isUnesco is True, "apply_city_consensus must preserve isUnesco=True across duplicates"
    assert kinkaku.unescoId == "688", f"Expected unescoId='688' preserved, got {kinkaku.unescoId!r}"
    assert kinkaku.unescoName and "Kyoto" in kinkaku.unescoName

    kml = build_city_kml(merged)
    assert "[UNESCO World Heritage]" in kml
    assert "Kinkaku-ji" in kml


def test_area_b_frontend_unesco_contract() -> None:
    """F10: Frontend TypeScript types, CityDrawer, Story Map client, and exportPack support UNESCO."""
    types_src = (WEB_DIR / "src/types/podatlas.ts").read_text(encoding="utf-8")
    drawer_src = (WEB_DIR / "src/components/CityDrawer.astro").read_text(encoding="utf-8")
    client_src = (WEB_DIR / "src/scripts/client.ts").read_text(encoding="utf-8")
    export_src = (WEB_DIR / "src/lib/exportPack.ts").read_text(encoding="utf-8")

    assert "isUnesco" in types_src
    assert "unescoId" in types_src
    assert "unescoName" in types_src
    assert "isUnesco" in drawer_src and "UNESCO" in drawer_src
    assert "isUnesco" in client_src and "UNESCO" in client_src
    assert "[UNESCO World Heritage]" in export_src


# ===========================================================================
# AREA C: National Park Service (NPS) Data API (F4, F9, F11)
# ===========================================================================


def test_area_c_settings_loads_nps_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """F4: PipelineSettings loads nps_api_key from PODATLAS_NPS_API_KEY and defaults to empty string."""
    monkeypatch.delenv("PODATLAS_NPS_API_KEY", raising=False)
    default_settings = PipelineSettings(_env_file=None)
    assert hasattr(default_settings, "nps_api_key"), "PipelineSettings must define nps_api_key (F4)"
    assert default_settings.nps_api_key == ""

    monkeypatch.setenv("PODATLAS_NPS_API_KEY", "test-nps-key-123")
    configured = PipelineSettings(_env_file=None)
    assert configured.nps_api_key == "test-nps-key-123"


def test_area_c_models_support_nps_source_and_standalone_places() -> None:
    """F4: EpisodeRecord(source='nps') and standalone PlaceOrGearRecord(source='nps', episodeId='') validate."""
    ep = EpisodeRecord(
        id="nps-audio-1",
        show="National Park Service",
        title="Ranger Walk",
        category="Travel",
        audioUrl="https://www.nps.gov/av/walk.mp3",
        durationSec=420,
        publishedAt="2026-01-15",
        summary="Official park audio.",
        source="nps",
    )
    assert ep.source == "nps"

    place = PlaceOrGearRecord(
        name="Old Faithful Geyser",
        type="Landmark",
        note="Iconic cone geyser.",
        lat=44.4605,
        lon=-110.8281,
        source="nps",
    )
    assert place.source == "nps"
    assert place.episodeId == ""
    assert place.timestampSec == 0


def test_area_c_nps_client_fetches_parks_audio_places_with_cache(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: NPSClient queries /parks, /audio, /places and caches responses on disk."""
    nps_mod = import_planned_module("podatlas_pipeline.nps")

    mock_http_server.add_route("/parks", make_nps_parks_payload())
    mock_http_server.add_route("/audio", make_nps_audio_payload())
    mock_http_server.add_route("/places", make_nps_places_payload())

    client = make_nps_client(
        nps_mod,
        api_key="fake-key",
        base_url=mock_http_server.base_url,
        cache_dir=tmp_path / "nps_cache",
        monkeypatch=monkeypatch,
    )

    parks = call_nps_parks(client, park_code="yell", limit=5)
    assert len(parks) >= 1
    first_req_count = len(mock_http_server.requests_log)
    assert first_req_count >= 1

    # Second identical call should hit disk cache if caching is enabled
    parks_again = call_nps_parks(client, park_code="yell", limit=5)
    assert len(parks_again) == len(parks)
    assert len(mock_http_server.requests_log) == first_req_count, "Expected disk cache hit on second NPS call"


def test_area_c_ingest_nps_cli_registered_with_expected_flags() -> None:
    """F9: podatlas-pipeline ingest-nps is registered with --park-code and --limit flags."""
    proc = run_cli(["ingest-nps", "--help"])
    assert proc.returncode == 0, f"ingest-nps --help failed: {proc.stderr}"
    assert "--park-code" in proc.stdout
    assert "--limit" in proc.stdout


def test_area_c_nps_audio_and_places_map_to_city_data(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: NPS park, audio, and places map to valid CityData with public-domain audio and geocoded places."""
    nps_mod = import_planned_module("podatlas_pipeline.nps")

    parks_payload = make_nps_parks_payload()
    audio_payload = make_nps_audio_payload()
    places_payload = make_nps_places_payload()
    mock_http_server.add_route("/parks", parks_payload)
    mock_http_server.add_route("/audio", audio_payload)
    mock_http_server.add_route("/places", places_payload)

    client = make_nps_client(
        nps_mod,
        api_key="fake-key",
        base_url=mock_http_server.base_url,
        cache_dir=tmp_path / "nps_cache",
        monkeypatch=monkeypatch,
    )
    cities = call_nps_park_builder(
        nps_mod, client, parks_payload, audio_payload, places_payload, park_code="yell", limit=1
    )
    assert len(cities) >= 1
    city = cities[0]

    assert isinstance(city, CityData)
    assert any(ep.source == "nps" for ep in city.episodes)
    assert any(p.source == "nps" and p.lat is not None and p.lon is not None for p in city.placesAndGear)


def test_area_c_frontend_nps_contract() -> None:
    """F11: Frontend types, CityDrawer, and exportPack support NPS source and standalone places."""
    types_src = (WEB_DIR / "src/types/podatlas.ts").read_text(encoding="utf-8")
    drawer_src = (WEB_DIR / "src/components/CityDrawer.astro").read_text(encoding="utf-8")
    export_src = (WEB_DIR / "src/lib/exportPack.ts").read_text(encoding="utf-8")

    assert re.search(r"source\??\s*:\s*[^;\n]*['\"]nps['\"]", types_src), (
        "web/src/types/podatlas.ts must include 'nps' in source union type"
    )
    assert "NPS Audio" in drawer_src
    assert re.search(r"\bbuildCityKml\b", export_src) and re.search(r"\bbuildTripItineraryText\b", export_src)


# ===========================================================================
# AREA D: Wikidata P840 Books & Cold-Start City Enrichment (F4, F7, F8, F11)
# ===========================================================================


def test_area_d_book_record_supports_wikidata_fields_and_defaults() -> None:
    """F4: BookRecord exposes wikidataId/source and defaultable episodeId/show/timestampSec."""
    book = BookRecord(
        title="The Old Capital",
        author="Yasunari Kawabata",
        wikidataId="Q1209876",
        source="wikidata",
    )
    assert book.wikidataId == "Q1209876"
    assert book.source == "wikidata"
    assert book.episodeId == ""
    assert book.show == ""
    assert book.timestampSec == 0


def test_area_d_wikidata_fallback_verifier_when_open_library_misses(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: WikidataBookClient verifies a book when OpenLibraryVerifier returns None."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")

    # Serve empty Open Library result and valid Wikidata SPARQL response
    mock_http_server.add_route("/ol/search.json", {"numFound": 0, "docs": []})
    add_wikidata_sparql_route(
        mock_http_server,
        [
            make_sparql_binding(
                "Q1209876",
                "The Old Capital",
                "Yasunari Kawabata",
                open_library_id="OL99887W",
                isbn13="9781593760328",
            )
        ],
    )
    mock_http_server.add_route(
        "/w/api.php",
        {
            "search": [
                {
                    "id": "Q1209876",
                    "label": "The Old Capital",
                    "description": "1962 novel by Yasunari Kawabata set in Kyoto",
                }
            ]
        },
    )

    wd_client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)

    import podatlas_pipeline.verify_books as vb_mod

    monkeypatch.setattr(vb_mod, "OPEN_LIBRARY_SEARCH_URL", f"{mock_http_server.base_url}/ol/search.json")
    ol_sig = inspect.signature(OpenLibraryVerifier.__init__)
    ol_kwargs: dict[str, Any] = {"cache_path": tmp_path / "ol_cache.json"}
    if "wikidata_client" in ol_sig.parameters:
        ol_kwargs["wikidata_client"] = wd_client
    elif "fallback_verifier" in ol_sig.parameters:
        ol_kwargs["fallback_verifier"] = wd_client

    verifier = OpenLibraryVerifier(**ol_kwargs)
    verified = verifier.verify("The Old Capital", "Yasunari Kawabata")
    assert verified is not None, "Expected Wikidata fallback to verify book when Open Library misses"
    assert isinstance(verified, VerifiedBook)
    assert getattr(verified, "wikidata_id", None) == "Q1209876"


def test_area_d_geonames_index_parses_geonames_id(tmp_path: Path) -> None:
    """F8: GeoCity stores geonames_id parsed from cities5000.txt."""
    idx = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))
    kyoto = idx.lookup("Kyoto", "Japan")
    assert kyoto is not None
    assert hasattr(kyoto, "geonames_id"), "GeoCity must expose geonames_id (F8)"
    assert str(kyoto.geonames_id) == "1857910"


def test_area_d_wikidata_cold_start_city_books_query(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: WikidataBookClient queries P1566+P840 to return verified books set in a city."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")

    add_wikidata_sparql_route(
        mock_http_server,
        [
            make_sparql_binding(
                "Q1209876",
                "The Old Capital",
                "Yasunari Kawabata",
                open_library_id="OL99887W",
            ),
            make_sparql_binding(
                "Q1197128",
                "The Temple of the Golden Pavilion",
                "Yukio Mishima",
            ),
        ],
    )
    client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)
    query_fn = get_wikidata_city_books_fn(client)
    books = query_fn("1857910", limit=5) if "limit" in inspect.signature(query_fn).parameters else query_fn("1857910")
    assert len(books) >= 2
    titles = {b.title for b in books}
    assert "The Old Capital" in titles


def test_area_d_extract_cli_registers_wikidata_and_cold_start_flags() -> None:
    """F7/F8: podatlas-pipeline extract exposes --skip-wikidata, --fill-cold-start-books, --min-city-books."""
    proc = run_cli(["extract", "--help"])
    assert proc.returncode == 0, f"extract --help failed: {proc.stderr}"
    assert "--skip-wikidata" in proc.stdout
    assert "--fill-cold-start-books" in proc.stdout
    assert "--min-city-books" in proc.stdout


def test_area_d_frontend_wikidata_contract() -> None:
    """F11: Frontend guards findEpisode('') and renders Wikidata book badge without bogus 00:00 button."""
    types_src = (WEB_DIR / "src/types/podatlas.ts").read_text(encoding="utf-8")
    drawer_src = (WEB_DIR / "src/components/CityDrawer.astro").read_text(encoding="utf-8")

    assert "wikidataId" in types_src
    assert re.search(r"source\??\s*:\s*[^;\n]*['\"]wikidata['\"]", types_src), (
        "web/src/types/podatlas.ts must include 'wikidata' in BookRecord source union"
    )
    assert "Wikidata" in drawer_src and re.search(r"['\"]wikidata['\"]", drawer_src), (
        "CityDrawer.astro must handle source === 'wikidata' badge rendering"
    )

    # Parse findEpisode function body and verify it guards empty input and does NOT unconditionally
    # fall back to city.episodes[0].
    fn_match = re.search(
        r"function\s+findEpisode\s*\(([^)]*)\)[^{]*\{(.*?)\n\}",
        drawer_src,
        flags=re.DOTALL,
    )
    assert fn_match is not None, "Expected findEpisode(...) helper in CityDrawer.astro"
    fn_body = fn_match.group(2)
    assert not re.search(r"(\|\||\?\?)\s*city\.episodes\[0\]", fn_body), (
        f"findEpisode must NOT fall back to city.episodes[0]: {fn_body!r}"
    )
    assert re.search(r"return\s+(undefined|null)\b", fn_body), (
        f"findEpisode must guard empty input and return undefined/null: {fn_body!r}"
    )


# ===========================================================================
# AREA E: shows.csv 100+ Scaling & Schema Backward Compatibility (F4, F12)
# ===========================================================================


def test_area_e_shows_csv_has_at_least_100_rows_and_exact_header() -> None:
    """F12: pipeline/shows.csv has exact required header and >= 100 show rows."""
    with SHOWS_CSV.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = [r for r in reader if any(cell.strip() for cell in r)]

    assert header == ["slug", "title", "category", "rss_url", "podcast_index_id", "max_episodes"]
    assert len(rows) >= 100, f"Expected >= 100 shows in pipeline/shows.csv, found {len(rows)}"


def test_area_e_shows_csv_strict_categories_and_valid_urls() -> None:
    """F12: All shows.csv rows use allowed categories and valid http(s):// RSS URLs."""
    shows = load_shows_csv(SHOWS_CSV)
    allowed = {"Travel", "History", "Food", "Culture"}
    assert len(shows) >= 100
    for show in shows:
        assert show.category in allowed, f"Invalid category {show.category!r} for {show.slug}"
        assert show.rss_url.startswith(("http://", "https://")), f"Invalid URL for {show.slug}: {show.rss_url}"


def test_area_e_shows_csv_unique_slugs_and_normalized_urls() -> None:
    """F12: All shows.csv slugs and normalized RSS URLs are strictly unique."""
    shows = load_shows_csv(SHOWS_CSV)
    slugs = [s.slug for s in shows]
    norm_urls = [normalize_feed_url(s.rss_url) for s in shows]
    assert len(slugs) == len(set(slugs)), "Duplicate slugs found in shows.csv"
    assert len(norm_urls) == len(set(norm_urls)), "Duplicate normalized RSS URLs found in shows.csv"


def test_area_e_shows_csv_contains_verified_tier2_english_shows() -> None:
    """F12: pipeline/shows.csv includes verified Tier-2 English shows named in ORIGINAL_REQUEST.md."""
    shows = load_shows_csv(SHOWS_CSV)
    titles_lower = {s.title.lower() for s in shows}
    expected_substrings = [
        "bowery boys",
        "three castles burning",
        "the ancients",
        "off menu",
        "japan station",
    ]
    for expected in expected_substrings:
        assert any(expected in t for t in titles_lower), (
            f"Expected Tier-2 show matching '{expected}' in pipeline/shows.csv"
        )


def test_area_e_existing_seed_cities_validate_with_backward_compat_defaults() -> None:
    """F4: All 10 existing web/public/data/*.json files parse cleanly with backward-compatible defaults."""
    data_files = sorted((WEB_PUBLIC_DIR / "data").glob("*.json"))
    assert len(data_files) >= 10
    for path in data_files:
        city = CityData.model_validate_json(path.read_text(encoding="utf-8"))
        for ep in city.episodes:
            assert getattr(ep, "source", None) == "podcast"
        for book in city.books:
            assert getattr(book, "source", None) == "podcast"
            assert getattr(book, "wikidataId", "MISSING") is None
        for place in city.placesAndGear:
            assert getattr(place, "source", None) == "podcast"
            assert getattr(place, "isUnesco", None) is False
            assert getattr(place, "unescoId", "MISSING") is None


def test_area_e_validate_output_passes_on_web_public() -> None:
    """F4/F12: validate_output succeeds cleanly on web/public."""
    stats = validate_output(WEB_PUBLIC_DIR)
    assert stats["cities"] >= 10
    assert stats["episodes"] >= 10
