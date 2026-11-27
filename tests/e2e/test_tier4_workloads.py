"""Tier 4 E2E Tests: Full Realistic End-to-End Subprocess, Pipeline & Frontend Workloads."""

from __future__ import annotations

import inspect
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from podatlas_pipeline.cli import validate_output
from podatlas_pipeline.export_static import apply_city_consensus, export_static_site_data
from podatlas_pipeline.geocode import GeoNamesIndex
from podatlas_pipeline.models import BookRecord, CityData
from podatlas_pipeline.podcast_index import load_shows_csv

from conftest import (
    SHOWS_CSV,
    WEB_DIR,
    WEB_PUBLIC_DIR,
    LocalMockServer,
    add_wikidata_sparql_route,
    call_nps_park_builder,
    get_wikidata_city_books_fn,
    import_planned_module,
    make_nps_audio_payload,
    make_nps_client,
    make_nps_parks_payload,
    make_nps_places_payload,
    make_sparql_binding,
    make_substack_rss_xml,
    make_wikidata_client,
    require_web_node_modules,
    run_cli,
    write_geonames_fixture,
    write_unesco_fixture,
)


# ===========================================================================
# Workload 1: Full Substack Transcribe CLI Subprocess (Zero Whisper Cost)
# ===========================================================================


def test_workload_1_substack_transcribe_cli_zero_whisper_and_idempotent(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """Workload 1: podatlas-pipeline transcribe against local Substack RSS caches content_encoded and is idempotent."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/substack-feed.xml", rss_xml, content_type="application/rss+xml")

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"slow-kyoto,Slow Kyoto Dispatch,History,{mock_http_server.base_url}/substack-feed.xml,,5\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    env = {
        "PODATLAS_SHOWS_CSV": str(shows_csv),
        "PODATLAS_CACHE_DIR": str(cache_dir),
        "PODATLAS_OUTPUT_DIR": str(tmp_path / "out"),
        "PODATLAS_MIN_DURATION_SEC": "60",
    }

    first = run_cli(["transcribe", "--max-episodes", "1"], env_overrides=env)
    assert first.returncode == 0, f"First transcribe failed: {first.stderr}"

    cached_files = list((cache_dir / "transcripts").glob("*.json"))
    assert len(cached_files) == 1
    payload = json.loads(cached_files[0].read_text(encoding="utf-8"))
    assert payload["transcriptSource"] == "content_encoded"
    assert len(payload["segments"]) >= 1
    joined = " ".join(s["text"] for s in payload["segments"])
    assert "Kinkaku-ji" in joined
    assert "console.log" not in joined

    # Second run must be idempotent and skip the already-cached episode
    second = run_cli(["transcribe", "--max-episodes", "1"], env_overrides=env)
    assert second.returncode == 0, f"Second transcribe failed: {second.stderr}"
    stats_second = json.loads(second.stdout)
    assert stats_second.get("skippedAlreadyCached", 0) >= 1


# ===========================================================================
# Workload 2: Full Two-Pass CLI (transcribe -> extract -> validate)
# ===========================================================================


def test_workload_2_full_two_pass_cli_transcribe_extract_validate(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """Workload 2: End-to-end CLI transcribe -> extract (mock LLM + offline GeoNames/UNESCO) -> validate."""
    unesco_mod = import_planned_module("podatlas_pipeline.unesco")

    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/feed.xml", rss_xml, content_type="application/rss+xml")

    llm_payload = {
        "isDestinationFocused": True,
        "primaryCities": [
            {"city": "Kyoto", "country": "Japan", "tagline": "Golden temples and quiet cedar lanes."}
        ],
        "summary": "Walking tour of Kinkaku-ji and Nishiki Market in northern Kyoto.",
        "books": [
            {
                "title": "The Temple of the Golden Pavilion",
                "author": "Yukio Mishima",
                "quote": "Captures the atmospheric tension of postwar Kyoto.",
                "timestampSec": 120,
                "endorsement": "strong_rec",
            }
        ],
        "placesAndGear": [
            {
                "name": "Kinkaku-ji",
                "type": "Landmark",
                "note": "Golden Pavilion beside its mirror pond.",
                "timestampSec": 60,
                "endorsement": "strong_rec",
            },
            {
                "name": "Compact Travel Umbrella",
                "type": "Gear",
                "note": "Essential for sudden Kyoto afternoon showers.",
                "timestampSec": 180,
                "endorsement": "strong_rec",
            },
        ],
    }
    mock_http_server.add_route(
        "/v1/chat/completions",
        {
            "id": "chatcmpl-e2e-2",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(llm_payload)}}],
        },
    )

    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"slow-kyoto,Slow Kyoto Dispatch,History,{mock_http_server.base_url}/feed.xml,,5\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "public"
    write_geonames_fixture(cache_dir)

    # Pre-seed offline UNESCO fixture under any expected cache filename & subdirectory layout
    for fname in ("whc001.geojson", "unesco_whc001.geojson", "unesco_sites.geojson"):
        write_unesco_fixture(cache_dir, filename=fname)
    for fname in ("whc001.geojson", "unesco_whc001.geojson"):
        write_unesco_fixture(cache_dir / "unesco", filename=fname)
    for attr in ("UNESCO_CACHE_FILENAME", "DEFAULT_CACHE_FILENAME", "CACHE_FILENAME"):
        custom_name = getattr(unesco_mod, attr, None)
        if isinstance(custom_name, str) and custom_name:
            write_unesco_fixture(cache_dir, filename=custom_name)
            write_unesco_fixture(cache_dir / "unesco", filename=custom_name)

    env = {
        "PODATLAS_SHOWS_CSV": str(shows_csv),
        "PODATLAS_CACHE_DIR": str(cache_dir),
        "PODATLAS_OUTPUT_DIR": str(out_dir),
        "PODATLAS_LLM_BASE_URL": f"{mock_http_server.base_url}/v1",
        "PODATLAS_MIN_DURATION_SEC": "60",
    }

    p_transcribe = run_cli(["transcribe", "--max-episodes", "1"], env_overrides=env)
    assert p_transcribe.returncode == 0, f"transcribe failed: {p_transcribe.stderr}"

    p_extract = run_cli(
        ["extract", "--max-episodes", "1", "--skip-book-verify", "--skip-place-geocode", "--reprocess"],
        env_overrides=env,
    )
    assert p_extract.returncode == 0, f"extract failed: {p_extract.stderr}"

    # Verify all 6 static output artifact types exist and are valid
    assert (out_dir / "cities.geojson").is_file()
    assert (out_dir / "data" / "kyoto-japan.json").is_file()
    assert (out_dir / "maps" / "kyoto-japan.kml").is_file()
    assert (out_dir / "feeds" / "kyoto-japan.xml").is_file()
    assert (out_dir / "search-index.json").is_file()
    assert (out_dir / "sitemap.xml").is_file()

    kml_text = (out_dir / "maps" / "kyoto-japan.kml").read_text(encoding="utf-8")
    ET.fromstring(kml_text)
    assert "[UNESCO World Heritage]" in kml_text
    assert "Compact Travel Umbrella" not in kml_text

    p_validate = run_cli(["validate", "--output-dir", str(out_dir)], env_overrides=env)
    assert p_validate.returncode == 0, f"validate failed: {p_validate.stderr}"
    val_json = json.loads(p_validate.stdout)
    assert val_json["cities"] == 1
    assert val_json["episodes"] == 1


# ===========================================================================
# Workload 3: NPS Ingestion (No-Key Graceful Skip + Populated Export & Validate)
# ===========================================================================


def test_workload_3_nps_ingestion_and_static_export(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workload 3: CLI ingest-nps no-key graceful skip + populated NPS client -> export -> CLI validate."""
    # Part 1: No-key CLI graceful degradation
    no_key_proc = run_cli(
        ["ingest-nps", "--park-code", "yell", "--limit", "1"],
        env_overrides={
            "PODATLAS_CACHE_DIR": str(tmp_path / "cache"),
            "PODATLAS_OUTPUT_DIR": str(tmp_path / "out_skip"),
        },
    )
    assert no_key_proc.returncode == 0, f"ingest-nps no-key failed: {no_key_proc.stderr}"
    skip_json = json.loads(no_key_proc.stdout.strip())
    assert skip_json.get("skipped") is True

    # Part 2: Populated offline NPS API -> CityData -> export -> CLI validate
    nps_mod = import_planned_module("podatlas_pipeline.nps")

    parks_payload = make_nps_parks_payload()
    audio_payload = make_nps_audio_payload()
    places_payload = make_nps_places_payload()
    mock_http_server.add_route("/parks", parks_payload)
    mock_http_server.add_route("/audio", audio_payload)
    mock_http_server.add_route("/places", places_payload)

    client = make_nps_client(
        nps_mod,
        api_key="test-key",
        base_url=mock_http_server.base_url,
        cache_dir=tmp_path / "nps_cache",
        monkeypatch=monkeypatch,
    )
    cities = call_nps_park_builder(
        nps_mod, client, parks_payload, audio_payload, places_payload, park_code="yell", limit=1
    )

    out_dir = tmp_path / "nps_public"
    export_static_site_data(cities, out_dir)
    p_val = run_cli(["validate", "--output-dir", str(out_dir)])
    assert p_val.returncode == 0, f"validate failed on NPS output: {p_val.stderr}"
    val_stats = json.loads(p_val.stdout)
    assert val_stats["cities"] >= 1
    assert val_stats["episodes"] >= 1


# ===========================================================================
# Workload 4: Cold-Start City Book Enrichment (--fill-cold-start-books)
# ===========================================================================


def test_workload_4_cold_start_city_books_enrichment_and_export(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workload 4: Cold-start city enriched via Wikidata P1566+P840 exports and validates cleanly."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")

    add_wikidata_sparql_route(
        mock_http_server,
        [
            make_sparql_binding("Q1001", "The Old Capital", "Yasunari Kawabata", open_library_id="OL1001W"),
            make_sparql_binding("Q1002", "The Makioka Sisters", "Junichiro Tanizaki", open_library_id="OL1002W"),
            make_sparql_binding("Q1003", "Memoirs of a Geisha", "Arthur Golden", open_library_id="OL1003W"),
        ],
    )

    geonames = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))
    kyoto_geo = geonames.lookup("Kyoto", "Japan")
    assert kyoto_geo is not None and hasattr(kyoto_geo, "geonames_id")

    wd_client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)
    city_fn = get_wikidata_city_books_fn(wd_client)
    cold_books = city_fn(str(kyoto_geo.geonames_id))
    assert len(cold_books) >= 3

    city = CityData(
        id="kyoto-japan",
        name="Kyoto",
        country="Japan",
        lat=kyoto_geo.lat,
        lon=kyoto_geo.lon,
        primaryCategory="History",
        tagline="Ancient imperial capital.",
        books=[
            BookRecord(
                title=b.title,
                author=b.author,
                openLibraryKey=getattr(b, "open_library_key", getattr(b, "openLibraryKey", None)),
                wikidataId=getattr(b, "wikidata_id", getattr(b, "wikidataId", None)),
                source="wikidata",
            )
            for b in cold_books[:3]
        ],
    )
    city = apply_city_consensus(city)
    out_dir = tmp_path / "cold_public"
    export_static_site_data([city], out_dir)
    stats = validate_output(out_dir)
    assert stats["cities"] == 1
    assert stats["books"] == 3


# ===========================================================================
# Workload 5: Frontend Full Verification (typecheck + test + Astro build)
# ===========================================================================


def test_workload_5_frontend_typecheck_test_and_build() -> None:
    """Workload 5: Astro frontend passes npm run typecheck, npm test, and npm run build."""
    require_web_node_modules()

    for script in ("typecheck", "test", "build"):
        proc = subprocess.run(
            ["npm", "run", script],
            cwd=str(WEB_DIR),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert proc.returncode == 0, (
            f"`npm run {script}` failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    dist_dir = WEB_DIR / "dist"
    assert (dist_dir / "index.html").is_file(), "Astro build did not produce web/dist/index.html"
    city_pages = list((dist_dir / "city").glob("*/index.html"))
    assert len(city_pages) >= 10, f"Expected >= 10 pre-rendered city pages, found {len(city_pages)}"


# ===========================================================================
# Workload 6: Production Catalog & Static Site Integrity Workload
# ===========================================================================


def test_workload_6_production_catalog_and_static_site_integrity() -> None:
    """Workload 6: Real pipeline/shows.csv (100+ shows) & web/public static site pass full validation."""
    shows = load_shows_csv(SHOWS_CSV)
    assert len(shows) >= 100, f"Expected >= 100 shows in pipeline/shows.csv, got {len(shows)}"

    categories = {s.category for s in shows}
    assert categories == {"Travel", "History", "Food", "Culture"}, (
        f"Expected all 4 categories represented in shows.csv, got {categories}"
    )

    proc = run_cli(["validate", "--output-dir", str(WEB_PUBLIC_DIR)])
    assert proc.returncode == 0, f"CLI validate on web/public failed: {proc.stderr}"
    stats = json.loads(proc.stdout)
    assert stats["cities"] >= 10
    assert stats["episodes"] >= 10
    assert stats["books"] >= 10
    assert stats["placesAndGear"] >= 10
