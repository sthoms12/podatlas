"""Tier 2 E2E Tests: Boundary, Negative, Malformed Input & Graceful Degradation (Areas A–E)."""

from __future__ import annotations

import csv
import inspect
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from podatlas_pipeline.cli import merge_extraction_into_cities, validate_output
from podatlas_pipeline.export_static import build_city_kml, build_search_index
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
from podatlas_pipeline.podcast_index import (
    RawEpisode,
    ShowConfig,
    check_show_feed,
    fetch_rss_episodes,
    is_skippable_episode,
    parse_content_encoded_to_segments,
)

from conftest import (
    LONG_SUBSTACK_ESSAY_HTML,
    SHOWS_CSV,
    LocalMockServer,
    add_wikidata_sparql_route,
    call_nps_park_builder,
    call_nps_parks,
    get_wikidata_city_books_fn,
    get_wikidata_verify_fn,
    import_planned_module,
    make_mixed_city_dict,
    make_nps_client,
    make_nps_parks_payload,
    make_sparql_binding,
    make_substack_rss_xml,
    make_wikidata_client,
    run_cli,
    write_geonames_fixture,
    write_unesco_fixture,
)


def _load_unesco_index(tmp_path: Path) -> Any:
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


# ===========================================================================
# AREA A BOUNDARIES: Substack <content:encoded> (6 tests)
# ===========================================================================


def test_boundary_a_short_content_encoded_does_not_skip_whisper(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """Short show notes under min_chars MUST NOT be cached as transcriptSource='content_encoded'."""
    short_html = "<p>Brief show notes with links to our newsletter.</p>"
    # Direct public API check: HTML-to-segments converter returns [] below min_chars
    assert parse_content_encoded_to_segments(short_html) == []

    rss_xml = make_substack_rss_xml(mock_http_server.base_url, content_html=short_html)
    mock_http_server.add_route("/short.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"short-show,Short Notes Show,Travel,{mock_http_server.base_url}/short.xml,,5\n",
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

    # Anti-vacuous guard: CLI must actually have fetched the RSS feed and reached episode processing
    requested_paths = [req["path"] for req in mock_http_server.requests_log]
    assert any("/short.xml" in p for p in requested_paths), (
        f"CLI never fetched /short.xml (rc={proc.returncode}, stderr={proc.stderr!r})"
    )
    assert proc.returncode != 2, f"CLI failed with argparse error: {proc.stderr}"

    attempted_audio = any("/audio/" in p for p in requested_paths)
    combined_output = f"{proc.stdout}\n{proc.stderr}".lower()
    if proc.returncode == 0:
        stats = json.loads(proc.stdout)
        assert stats.get("cachedFromRss", 0) == 0
        assert stats.get("cachedFromContentEncoded", 0) == 0
    else:
        assert attempted_audio or any(
            tok in combined_output for tok in ("whisper", "audio", "404", "download")
        ), f"Expected Whisper/audio fallback attempt, got rc={proc.returncode}, out={combined_output!r}"

    transcript_dir = cache_dir / "transcripts"
    cached_files = list(transcript_dir.glob("*.json")) if transcript_dir.exists() else []
    for f in cached_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data.get("transcriptSource") != "content_encoded", (
            "Short <content:encoded> below min_chars must NOT bypass Whisper"
        )


def test_boundary_a_podcast_transcript_takes_precedence_over_content_encoded(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """<podcast:transcript> tag takes precedence over <content:encoded> when both exist."""
    vtt_body = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:08.000\n"
        "Welcome to the timestamped VTT transcript for Kyoto.\n"
    )
    mock_http_server.add_route("/ep1.vtt", vtt_body, content_type="text/vtt")
    rss_xml = make_substack_rss_xml(
        mock_http_server.base_url,
        content_html=LONG_SUBSTACK_ESSAY_HTML,
        transcript_path="/ep1.vtt",
    )
    mock_http_server.add_route("/both.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"both-show,Both Sources Show,History,{mock_http_server.base_url}/both.xml,,5\n",
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
    assert proc.returncode == 0, f"transcribe failed: {proc.stderr}"
    cached_files = list((cache_dir / "transcripts").glob("*.json"))
    assert len(cached_files) == 1
    data = json.loads(cached_files[0].read_text(encoding="utf-8"))
    assert data["transcriptSource"] == "rss_tag"
    assert "timestamped VTT transcript" in " ".join(s["text"] for s in data["segments"])


def test_boundary_a_script_only_or_malformed_html_produces_no_content_encoded_cache(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """Script/style-only or malformed HTML yields zero usable segments and is not cached."""
    bad_html = (
        "<script>" + ("var x = 1; " * 200) + "</script>"
        "<style>" + (".foo { color: red; } " * 200) + "</style>"
        "<p>   <br>   </p>"
    )
    # Direct public API check: HTML-to-segments converter returns [] for script/style-only HTML
    assert parse_content_encoded_to_segments(bad_html) == []

    rss_xml = make_substack_rss_xml(mock_http_server.base_url, content_html=bad_html)
    mock_http_server.add_route("/script-only.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"bad-html,Script Only Show,Culture,{mock_http_server.base_url}/script-only.xml,,5\n",
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

    # Anti-vacuous guard: CLI must actually have fetched /script-only.xml and reached episode processing
    requested_paths = [req["path"] for req in mock_http_server.requests_log]
    assert any("/script-only.xml" in p for p in requested_paths), (
        f"CLI never fetched /script-only.xml (rc={proc.returncode}, stderr={proc.stderr!r})"
    )
    assert proc.returncode != 2, f"CLI failed with argparse error: {proc.stderr}"

    attempted_audio = any("/audio/" in p for p in requested_paths)
    combined_output = f"{proc.stdout}\n{proc.stderr}".lower()
    if proc.returncode == 0:
        stats = json.loads(proc.stdout)
        assert stats.get("cachedFromRss", 0) == 0
        assert stats.get("cachedFromContentEncoded", 0) == 0
    else:
        assert attempted_audio or any(
            tok in combined_output for tok in ("whisper", "audio", "404", "download")
        ), f"Expected Whisper/audio fallback attempt, got rc={proc.returncode}, out={combined_output!r}"

    transcript_dir = cache_dir / "transcripts"
    cached_files = list(transcript_dir.glob("*.json")) if transcript_dir.exists() else []
    for f in cached_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data.get("transcriptSource") != "content_encoded"


def test_boundary_a_non_audio_enclosures_rejected(mock_http_server: LocalMockServer) -> None:
    """Non-audio enclosures (.jpg, .html) must not be accepted as audio_url."""
    rss_xml = make_substack_rss_xml(
        mock_http_server.base_url,
        audio_path="/images/cover.jpg?size=large",
        audio_mime="image/jpeg",
    )
    mock_http_server.add_route("/img.xml", rss_xml, content_type="application/rss+xml")
    show = ShowConfig(
        slug="img-only",
        title="Image Only Feed",
        category="Travel",
        rss_url=f"{mock_http_server.base_url}/img.xml",
        max_episodes=5,
    )
    episodes = fetch_rss_episodes(show)
    assert episodes == [] or all(ep.audio_url == "" for ep in episodes)


def test_boundary_a_empty_feed_and_http_404_handled_cleanly(
    mock_http_server: LocalMockServer,
) -> None:
    """check_show_feed handles HTTP 404 and empty RSS feeds cleanly without crashing."""
    empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    mock_http_server.add_route("/empty.xml", empty_rss, content_type="application/rss+xml")

    empty_show = ShowConfig(
        slug="empty",
        title="Empty",
        category="Travel",
        rss_url=f"{mock_http_server.base_url}/empty.xml",
    )
    empty_report = check_show_feed(empty_show)
    assert empty_report["ok"] is True
    assert empty_report["episodesFound"] == 0

    missing_show = ShowConfig(
        slug="missing",
        title="Missing",
        category="Travel",
        rss_url=f"{mock_http_server.base_url}/does-not-exist.xml",
    )
    missing_report = check_show_feed(missing_show)
    assert missing_report["ok"] is False
    assert missing_report["error"]


def test_boundary_a_short_duration_episode_skipped_by_filter() -> None:
    """Trailer episodes below min_duration_sec are filtered by is_skippable_episode."""
    ep = RawEpisode(
        id="ep-trailer",
        guid="g-trailer",
        show_slug="slow-kyoto",
        show_title="Slow Kyoto Dispatch",
        category="History",
        title="Season 2 Trailer",
        audio_url="https://example.org/trailer.m4a",
        duration_sec=120,
        published_at="2026-02-10",
        description="Short trailer.",
    )
    skip, reason = is_skippable_episode(ep, min_duration_sec=600)
    assert skip is True
    assert reason


# ===========================================================================
# AREA B BOUNDARIES: UNESCO World Heritage (6 tests)
# ===========================================================================


def test_boundary_b_unesco_rejects_far_away_coordinates(tmp_path: Path) -> None:
    """Anti-false-positive distance guard: Kinkaku-ji must NOT match when city is Rome."""
    idx = _load_unesco_index(tmp_path)
    match = idx.match("Kinkaku-ji", 41.8902, 12.4922)
    assert match is None, f"Far-away match should be rejected, got {match}"


def test_boundary_b_unesco_rejects_unrelated_name_near_site(tmp_path: Path) -> None:
    """Anti-false-positive name guard: unrelated coffee shop next to Kinkaku-ji must NOT match."""
    idx = _load_unesco_index(tmp_path)
    match = idx.match("Joe's Espresso Bar", 35.0394, 135.7292)
    assert match is None, f"Unrelated place name should not match UNESCO site, got {match}"


def test_boundary_b_unesco_handles_multipoint_and_malformed_components_list(tmp_path: Path) -> None:
    """MultiPoint geometry without geo_point_2d and malformed components_list are handled safely."""
    idx = _load_unesco_index(tmp_path)
    match = idx.match("Machu Picchu", -13.1631, -72.5450)
    assert match is not None, "Expected MultiPoint Machu Picchu site to load and match"
    assert str(getattr(match, "unesco_id", getattr(match, "id_no", ""))) == "274"


def test_boundary_b_gear_excluded_from_kml_even_with_coords() -> None:
    """Gear items are never emitted as KML Placemarks even if lat/lon are present."""
    city = CityData.model_validate(make_mixed_city_dict())
    kml = build_city_kml(city)
    root = ET.fromstring(kml)
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    names = [el.text for el in root.findall(".//kml:Placemark/kml:name", ns)]
    assert "Compact Travel Umbrella" not in names


def test_boundary_b_skip_unesco_bypasses_unesco_enrichment(tmp_path: Path) -> None:
    """When unesco_index is None (--skip-unesco), places are not flagged as UNESCO."""
    geonames = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))

    class _StubGeocoder(PlaceGeocoder):
        def geocode(self, *args: Any, **kwargs: Any) -> tuple[float, float] | None:
            return (35.0394, 135.7292)

    cached_ep = CachedEpisodeTranscript(
        id="ep-1",
        guid="g-1",
        showSlug="s",
        showTitle="Show",
        category="History",
        title="Kyoto",
        audioUrl="https://example.org/a.mp3",
        durationSec=1200,
        publishedAt="2026-02-10",
        transcriptSource="rss_tag",
        segments=[],
    )
    extraction = EpisodeExtraction(
        isDestinationFocused=True,
        primaryCities=[ExtractedCityRef(city="Kyoto", country="Japan", tagline="Temples")],
        summary="Visit Kinkaku-ji.",
        placesAndGear=[
            ExtractedPlaceOrGear(name="Kinkaku-ji", type="Landmark", note="Pavilion", timestampSec=60)
        ],
    )
    cities: dict[str, CityData] = {}
    merge_extraction_into_cities(cities, cached_ep, extraction, geonames, place_geocoder=_StubGeocoder())
    place = cities["kyoto-japan"].placesAndGear[0]
    assert getattr(place, "isUnesco", False) is False


def test_boundary_b_unesco_empty_name_returns_none(tmp_path: Path) -> None:
    """Empty or whitespace-only place names return None from UnescoIndex.match."""
    idx = _load_unesco_index(tmp_path)
    assert idx.match("", 35.0394, 135.7292) is None
    assert idx.match("   ", 35.0394, 135.7292) is None


# ===========================================================================
# AREA C BOUNDARIES: National Park Service (NPS) API (5 tests)
# ===========================================================================


def test_boundary_c_ingest_nps_without_api_key_exits_zero_with_skipped_json(tmp_path: Path) -> None:
    """podatlas-pipeline ingest-nps without API key exits 0 and prints JSON with skipped=true."""
    proc = run_cli(
        ["ingest-nps", "--limit", "1"],
        env_overrides={
            "PODATLAS_CACHE_DIR": str(tmp_path / "cache"),
            "PODATLAS_OUTPUT_DIR": str(tmp_path / "out"),
        },
    )
    assert proc.returncode == 0, f"Expected exit 0 without API key, got {proc.returncode}: {proc.stderr}"
    payload = json.loads(proc.stdout.strip())
    assert payload.get("skipped") is True


def test_boundary_c_nps_api_http_500_degrades_gracefully(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NPSClient degrades gracefully on HTTP 500 without unhandled crash."""
    nps_mod = import_planned_module("podatlas_pipeline.nps")
    mock_http_server.add_route("/parks", "Internal Server Error", status=500)

    client = make_nps_client(
        nps_mod,
        api_key="fake-key",
        base_url=mock_http_server.base_url,
        cache_dir=tmp_path / "nps_cache",
        monkeypatch=monkeypatch,
    )

    parks = call_nps_parks(client, limit=1)
    assert parks == []


def test_boundary_c_nps_park_with_empty_audio_and_places(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NPS park with empty /audio and /places still maps cleanly to a valid CityData."""
    nps_mod = import_planned_module("podatlas_pipeline.nps")

    parks_payload = make_nps_parks_payload()
    empty_audio = {"total": "0", "data": []}
    empty_places = {"total": "0", "data": []}
    mock_http_server.add_route("/parks", parks_payload)
    mock_http_server.add_route("/audio", empty_audio)
    mock_http_server.add_route("/places", empty_places)

    client = make_nps_client(
        nps_mod,
        api_key="fake-key",
        base_url=mock_http_server.base_url,
        cache_dir=tmp_path / "nps_cache",
        monkeypatch=monkeypatch,
    )
    cities = call_nps_park_builder(
        nps_mod, client, parks_payload, empty_audio, empty_places, park_code="yell", limit=1
    )
    assert len(cities) == 1
    city = cities[0]

    assert isinstance(city, CityData)
    assert city.episodes == []
    assert city.placesAndGear == []


def test_boundary_c_nps_place_without_coords_omitted_from_kml() -> None:
    """Standalone NPS place missing lat/lon is excluded from KML output."""
    city = CityData.model_validate(make_mixed_city_dict())
    city.placesAndGear.append(
        PlaceOrGearRecord(
            name="Unlocated Ranger Desk",
            type="Landmark",
            note="No coordinates available.",
            lat=None,
            lon=None,
            source="nps",
        )
    )
    kml = build_city_kml(city)
    assert "Unlocated Ranger Desk" not in kml


def test_boundary_c_standalone_nps_place_in_kml_has_no_zero_timestamp_suffix() -> None:
    """Standalone NPS place (empty episodeId/show) MUST NOT emit '(@ 00:00)' in KML."""
    city = CityData.model_validate(make_mixed_city_dict())
    kml = build_city_kml(city)
    root = ET.fromstring(kml)
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    for pm in root.findall(".//kml:Placemark", ns):
        name = pm.findtext("kml:name", default="", namespaces=ns)
        desc = pm.findtext("kml:description", default="", namespaces=ns)
        if name == "Cedar Trailhead Overlook":
            assert "(@ 00:00)" not in desc, f"Standalone NPS place leaked (@ 00:00): {desc!r}"
            break
    else:
        pytest.fail("Expected 'Cedar Trailhead Overlook' Placemark in KML")


# ===========================================================================
# AREA D BOUNDARIES: Wikidata P840 Books (6 tests)
# ===========================================================================


def test_boundary_d_wikidata_client_handles_http_500_and_malformed_json(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WikidataBookClient returns [] / None gracefully on HTTP 500 or malformed JSON."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")
    mock_http_server.add_route("/sparql", "<<<NOT JSON>>>", status=500)
    mock_http_server.add_route("/w/api.php", "<<<NOT JSON>>>", status=500)
    client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)

    verify_fn = get_wikidata_verify_fn(client)
    assert verify_fn("The Old Capital", "Yasunari Kawabata") is None

    city_fn = get_wikidata_city_books_fn(client)
    assert city_fn("1857910") == []


def test_boundary_d_wikidata_filters_raw_qid_labels(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wikidata client filters out items whose label is an unresolved Q-ID like 'Q987654'."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")
    add_wikidata_sparql_route(
        mock_http_server,
        [
            make_sparql_binding("Q987654", "Q987654", "Q111222"),
            make_sparql_binding("Q1209876", "The Old Capital", "Yasunari Kawabata"),
        ],
    )
    client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)
    city_fn = get_wikidata_city_books_fn(client)
    books = city_fn("1857910")
    titles = [b.title for b in books]
    assert "Q987654" not in titles
    assert "The Old Capital" in titles


def test_boundary_d_wikidata_deduplicates_duplicate_qids(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate SPARQL rows for the same Q-ID are deduplicated into one book."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")
    row = make_sparql_binding(
        "Q1209876",
        "The Old Capital",
        "Yasunari Kawabata",
        open_library_id="OL99887W",
    )
    add_wikidata_sparql_route(mock_http_server, [row, dict(row)])
    client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)
    city_fn = get_wikidata_city_books_fn(client)
    books = city_fn("1857910")
    assert len(books) == 1


def test_boundary_d_wikidata_normalizes_p648_open_library_keys(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare P648 Open Library work IDs like 'OL99887W' are normalized to '/works/OL99887W'."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")
    add_wikidata_sparql_route(
        mock_http_server,
        [
            make_sparql_binding(
                "Q1209876",
                "The Old Capital",
                "Yasunari Kawabata",
                open_library_id="OL99887W",
            )
        ],
    )
    client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)
    city_fn = get_wikidata_city_books_fn(client)
    books = city_fn("1857910")
    assert len(books) == 1
    ol_key = getattr(books[0], "open_library_key", getattr(books[0], "openLibraryKey", None))
    assert ol_key == "/works/OL99887W", f"Expected normalized '/works/OL99887W', got {ol_key!r}"


def test_boundary_d_build_search_index_formats_wikidata_book_cleanly() -> None:
    """build_search_index formats Wikidata cold-start books without bogus '(@ 00:00)' or empty show."""
    city = CityData.model_validate(make_mixed_city_dict())
    entries = build_search_index([city])
    wd_entries = [e for e in entries if e.kind == "Book" and e.title == "The Old Capital"]
    assert len(wd_entries) == 1
    item = wd_entries[0]
    assert "00:00" not in item.subtitle
    assert "()" not in item.subtitle
    assert not item.subtitle.endswith("· ")


def test_boundary_d_geonames_malformed_line_skipped_gracefully(tmp_path: Path) -> None:
    """GeoNamesIndex.from_geonames_file skips truncated/malformed TSV lines safely."""
    txt_path = write_geonames_fixture(tmp_path)
    txt_path.write_text(
        "corrupt\tline\n" + txt_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    idx = GeoNamesIndex.from_geonames_file(txt_path)
    assert idx.lookup("Kyoto", "Japan") is not None


# ===========================================================================
# AREA E BOUNDARIES: shows.csv & Static Validation (5 tests)
# ===========================================================================


def test_boundary_e_shows_csv_no_case_insensitive_duplicate_slugs() -> None:
    """Slugs in pipeline/shows.csv must be unique even case-insensitively."""
    with SHOWS_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    lower_slugs = [r["slug"].strip().lower() for r in rows]
    assert len(lower_slugs) == len(set(lower_slugs))


def test_boundary_e_shows_csv_no_blank_fields_and_positive_max_episodes() -> None:
    """Every shows.csv row has non-empty slug/title/rss_url and positive integer max_episodes."""
    with SHOWS_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 100
    for r in rows:
        assert r["slug"].strip()
        assert r["title"].strip()
        assert r["rss_url"].strip()
        max_ep = int(r["max_episodes"])
        assert max_ep > 0


def test_boundary_e_validate_output_empty_dir_and_schema_violation(tmp_path: Path) -> None:
    """validate_output returns 0 cities for an empty directory and rejects schema-invalid city JSON."""
    empty_stats = validate_output(tmp_path)
    assert empty_stats["cities"] == 0

    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "data" / "invalid-city.json").write_text(
        json.dumps({"id": "bad", "name": "Missing Required Fields"}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        validate_output(tmp_path)


def test_boundary_e_validate_output_rejects_corrupt_city_json(tmp_path: Path) -> None:
    """validate_output fails when a city data JSON file is corrupted."""
    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "cities.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "broken-city.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        validate_output(tmp_path)


def test_boundary_e_invalid_category_rejected_by_pydantic_models() -> None:
    """Pydantic models reject categories outside Travel|History|Food|Culture."""
    with pytest.raises(ValidationError):
        EpisodeRecord(
            id="ep-bad",
            show="Bad Show",
            title="Bad Cat",
            category="Sports",  # type: ignore[arg-type]
            audioUrl="",
            durationSec=600,
            publishedAt="2026-01-01",
            summary="Invalid category test.",
        )
