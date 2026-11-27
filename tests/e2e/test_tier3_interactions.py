"""Tier 3 E2E Tests: Pairwise Cross-Feature Interactions & Python↔TypeScript Parity."""

from __future__ import annotations

import inspect
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from podatlas_pipeline.cli import merge_extraction_into_cities, validate_output
from podatlas_pipeline.export_static import (
    apply_city_consensus,
    build_city_kml,
    build_city_rss_xml,
    build_search_index,
    export_static_site_data,
    load_existing_cities,
)
from podatlas_pipeline.extract import LLMExtractor
from podatlas_pipeline.geocode import GeoNamesIndex, PlaceGeocoder
from podatlas_pipeline.models import (
    BookRecord,
    CachedEpisodeTranscript,
    CityData,
    EpisodeExtraction,
    EpisodeRecord,
    ExtractedBook,
    ExtractedCityRef,
    ExtractedPlaceOrGear,
    PlaceOrGearRecord,
    TranscriptSegment,
)
from podatlas_pipeline.open_datasets import discover_from_locations, normalize_feed_url
from podatlas_pipeline.podcast_index import (
    ShowConfig,
    fetch_rss_episodes,
    load_shows_csv,
    parse_content_encoded_to_segments,
)
from podatlas_pipeline.verify_books import OpenLibraryVerifier

from conftest import (
    LONG_SUBSTACK_ESSAY_HTML,
    SHOWS_CSV,
    WEB_DIR,
    LocalMockServer,
    add_wikidata_sparql_route,
    import_planned_module,
    make_mixed_city_dict,
    make_nps_audio_payload,
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


def _extract_unesco_id(match: Any) -> str:
    """Read UNESCO site ID across common attribute names; assert non-empty (never silently default)."""
    for attr in ("unesco_id", "unescoId", "id_no", "id"):
        val = getattr(match, attr, None)
        if val is not None and str(val).strip():
            return str(val).strip()
    pytest.fail(
        f"UnescoIndex.match() result {match!r} did not expose a non-empty UNESCO ID "
        "(checked: unesco_id, unescoId, id_no, id)"
    )


def _invoke_ts_export_pack(city_dict: dict[str, Any]) -> dict[str, str]:
    """Execute web/src/lib/exportPack.ts (buildCityKml + buildTripItineraryText) via Node + esbuild."""
    require_web_node_modules()
    node_script = r"""
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const esbuild = require('esbuild');

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const city = JSON.parse(Buffer.concat(chunks).toString('utf8'));

const built = await esbuild.build({
  entryPoints: [path.resolve('src/lib/exportPack.ts')],
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
});
const code = built.outputFiles[0].text;
const mod = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const out = {
  kml: mod.buildCityKml(city),
  itinerary: mod.buildTripItineraryText(city),
};
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=str(WEB_DIR),
        input=json.dumps(city_dict),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Failed to execute TS exportPack.ts via Node:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def _parse_kml_placemarks(kml_xml: str) -> list[dict[str, str]]:
    root = ET.fromstring(kml_xml)
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    result: list[dict[str, str]] = []
    for pm in root.findall(".//kml:Placemark", ns):
        result.append(
            {
                "name": (pm.findtext("kml:name", default="", namespaces=ns) or "").strip(),
                "description": (pm.findtext("kml:description", default="", namespaces=ns) or "").strip(),
                "coordinates": (
                    pm.findtext("kml:Point/kml:coordinates", default="", namespaces=ns) or ""
                ).strip(),
            }
        )
    return result


# ===========================================================================
# 1. Substack -> LLM Extraction -> UNESCO Pre-Nominatim -> Static KML
# ===========================================================================


def test_interaction_substack_llm_unesco_export_kml(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """Substack <content:encoded> -> LLMExtractor -> UnescoIndex -> export_static -> KML UNESCO marker."""
    rss_xml = make_substack_rss_xml(mock_http_server.base_url)
    mock_http_server.add_route("/substack.xml", rss_xml, content_type="application/rss+xml")

    llm_extraction_json = {
        "isDestinationFocused": True,
        "primaryCities": [{"city": "Kyoto", "country": "Japan", "tagline": "Golden temples and matcha."}],
        "summary": "Walking essay covering Kinkaku-ji and Kyoto's wooden temples.",
        "books": [],
        "placesAndGear": [
            {
                "name": "Kinkaku-ji",
                "type": "Landmark",
                "note": "Golden Pavilion beside its mirror pond.",
                "timestampSec": 300,
                "endorsement": "strong_rec",
            }
        ],
    }
    mock_http_server.add_route(
        "/v1/chat/completions",
        {
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(llm_extraction_json)}}],
        },
    )

    show = ShowConfig(
        slug="slow-kyoto",
        title="Slow Kyoto Dispatch",
        category="History",
        rss_url=f"{mock_http_server.base_url}/substack.xml",
    )
    raw_eps = fetch_rss_episodes(show)
    assert len(raw_eps) == 1
    assert hasattr(raw_eps[0], "content_encoded") and raw_eps[0].content_encoded

    segments = parse_content_encoded_to_segments(
        raw_eps[0].content_encoded,
        duration_sec=raw_eps[0].duration_sec,
    )
    assert len(segments) >= 1, "Expected F2 HTML-to-segments converter to produce segments"
    joined_segments = " ".join(s.text for s in segments)
    assert "console.log" not in joined_segments
    assert "Kinkaku-ji" in joined_segments

    extractor = LLMExtractor(base_url=f"{mock_http_server.base_url}/v1")
    extraction = extractor.extract_episode(
        show.title,
        raw_eps[0].title,
        segments,
        subject_location=raw_eps[0].subject_location,
    )

    geonames = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))
    unesco_idx = _load_unesco_index(tmp_path)

    cached_ep = CachedEpisodeTranscript(
        id=raw_eps[0].id,
        guid=raw_eps[0].guid,
        showSlug=show.slug,
        showTitle=show.title,
        category=show.category,
        title=raw_eps[0].title,
        audioUrl=raw_eps[0].audio_url,
        durationSec=raw_eps[0].duration_sec,
        publishedAt=raw_eps[0].published_at,
        transcriptSource="content_encoded",
        segments=segments,
    )

    cities: dict[str, CityData] = {}
    merge_extraction_into_cities(cities, cached_ep, extraction, geonames, unesco_index=unesco_idx)

    out_dir = tmp_path / "public"
    export_static_site_data(list(cities.values()), out_dir)
    kml_text = (out_dir / "maps" / "kyoto-japan.kml").read_text(encoding="utf-8")
    assert "[UNESCO World Heritage]" in kml_text
    assert "Kinkaku-ji" in kml_text


# ===========================================================================
# 2. Open Library Miss -> Wikidata Fallback -> Consensus -> Search Index
# ===========================================================================


def test_interaction_open_library_miss_wikidata_fallback_consensus_search_index(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open Library miss falls back to Wikidata, then flows through consensus and build_search_index."""
    wd_mod = import_planned_module("podatlas_pipeline.wikidata_books")

    mock_http_server.add_route("/ol/search.json", {"numFound": 0, "docs": []})
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
    mock_http_server.add_route(
        "/w/api.php",
        {
            "search": [
                {
                    "id": "Q1209876",
                    "label": "The Old Capital",
                    "description": "Novel by Yasunari Kawabata",
                }
            ]
        },
    )

    import podatlas_pipeline.verify_books as vb_mod

    monkeypatch.setattr(vb_mod, "OPEN_LIBRARY_SEARCH_URL", f"{mock_http_server.base_url}/ol/search.json")

    wd_client = make_wikidata_client(wd_mod, mock_http_server.base_url, tmp_path, monkeypatch)

    ol_sig = inspect.signature(OpenLibraryVerifier.__init__)
    ol_kwargs: dict[str, Any] = {"cache_path": tmp_path / "ol_cache.json"}
    if "wikidata_client" in ol_sig.parameters:
        ol_kwargs["wikidata_client"] = wd_client
    elif "fallback_verifier" in ol_sig.parameters:
        ol_kwargs["fallback_verifier"] = wd_client
    verifier = OpenLibraryVerifier(**ol_kwargs)

    geonames = GeoNamesIndex.from_geonames_file(write_geonames_fixture(tmp_path))
    cached_ep = CachedEpisodeTranscript(
        id="ep-1",
        guid="g1",
        showSlug="slow-kyoto",
        showTitle="Slow Kyoto Dispatch",
        category="History",
        title="Kyoto Novels",
        audioUrl="https://example.org/a.m4a",
        durationSec=1800,
        publishedAt="2026-02-10",
        transcriptSource="content_encoded",
        segments=[],
    )
    extraction = EpisodeExtraction(
        isDestinationFocused=True,
        primaryCities=[ExtractedCityRef(city="Kyoto", country="Japan", tagline="Literary capital")],
        summary="Books set in Kyoto.",
        books=[
            ExtractedBook(
                title="The Old Capital",
                author="Yasunari Kawabata",
                quote="A lyrical portrait of Kyoto's seasonal festivals.",
                timestampSec=420,
            )
        ],
    )

    cities: dict[str, CityData] = {}
    merge_extraction_into_cities(cities, cached_ep, extraction, geonames, book_verifier=verifier)
    kyoto = apply_city_consensus(cities["kyoto-japan"])
    assert len(kyoto.books) == 1
    assert kyoto.books[0].wikidataId == "Q1209876"

    search_items = build_search_index([kyoto])
    assert any(item.kind == "Book" and item.title == "The Old Capital" for item in search_items)


# ===========================================================================
# 3. Podcast Book Precedence over Duplicate Wikidata Cold-Start Book
# ===========================================================================


def test_interaction_podcast_book_subsumes_duplicate_wikidata_book_in_consensus() -> None:
    """Podcast books rank strictly ahead of and subsume duplicate Wikidata cold-start books."""
    city = CityData(
        id="kyoto-japan",
        name="Kyoto",
        country="Japan",
        lat=35.0211,
        lon=135.7539,
        primaryCategory="History",
        tagline="Temples and literature.",
        books=[
            # Duplicate Wikidata book with ISBN, strong_rec, and longer quote
            BookRecord(
                title="The Old Capital",
                author="Yasunari Kawabata",
                isbn="9780140449266",
                wikidataId="Q1209876",
                episodeId="",
                show="",
                timestampSec=0,
                quote="An atmospheric Nobel-winning portrait of seasonal Kyoto festivals and cedar groves.",
                endorsement="strong_rec",
                source="wikidata",
            ),
            # Duplicate podcast book with NO ISBN, empty quote, casual_mention — MUST still subsume Wikidata copy
            BookRecord(
                title="The Old Capital",
                author="Yasunari Kawabata",
                isbn=None,
                episodeId="ep-1",
                show="Japan Station",
                timestampSec=600,
                quote="",
                endorsement="casual_mention",
                source="podcast",
            ),
            # Distinct Wikidata cold-start book starting with "A..." and strong_rec
            BookRecord(
                title="A Guide to Ancient Kyoto",
                author="Haruki Tanaka",
                isbn="9780140449999",
                wikidataId="Q222333",
                episodeId="",
                show="",
                timestampSec=0,
                quote="Comprehensive architectural guide to Kyoto shrines.",
                endorsement="strong_rec",
                source="wikidata",
            ),
            # Distinct podcast book starting with "Z..." and casual_mention — MUST still rank above "A Guide..."
            BookRecord(
                title="Zebra Chronicles of Kyoto",
                author="Kenji Sato",
                isbn=None,
                episodeId="ep-2",
                show="Slow Kyoto Dispatch",
                timestampSec=300,
                quote="",
                endorsement="casual_mention",
                source="podcast",
            ),
        ],
    )
    merged = apply_city_consensus(city)
    assert len(merged.books) == 3, f"Expected duplicate title subsumed (3 books total), got {[b.title for b in merged.books]}"

    old_cap = [b for b in merged.books if b.title == "The Old Capital"]
    assert len(old_cap) == 1
    assert old_cap[0].source == "podcast", "Podcast book must subsume duplicate Wikidata book"
    assert old_cap[0].episodeId == "ep-1"

    # All source == "podcast" books must rank strictly ahead of all source == "wikidata" books
    sources = [b.source for b in merged.books]
    first_wd_idx = sources.index("wikidata")
    assert all(s == "podcast" for s in sources[:first_wd_idx])
    assert all(s == "wikidata" for s in sources[first_wd_idx:])

    titles = [b.title for b in merged.books]
    assert titles.index("Zebra Chronicles of Kyoto") < titles.index("A Guide to Ancient Kyoto"), (
        f"Expected podcast book 'Zebra Chronicles of Kyoto' to rank before Wikidata book 'A Guide to Ancient Kyoto', got {titles}"
    )


# ===========================================================================
# 4. NPS Park + Audio + Places + UNESCO + Podcast Merged in KML
# ===========================================================================


def test_interaction_nps_merged_with_podcast_and_unesco_in_kml(tmp_path: Path) -> None:
    """NPS places + UNESCO match + podcast landmark render cleanly together in KML."""
    unesco_idx = _load_unesco_index(tmp_path)
    match = unesco_idx.match("Old Faithful Geyser", 44.5982, -110.5472)
    assert match is not None

    city = CityData(
        id="yellowstone-national-park-united-states",
        name="Yellowstone National Park",
        country="United States",
        lat=44.5982,
        lon=-110.5472,
        primaryCategory="Travel",
        tagline="Geothermal wonders and wildlife.",
        episodes=[
            EpisodeRecord(
                id="ep-pod-1",
                show="Zero To Travel",
                title="Yellowstone Backcountry",
                category="Travel",
                audioUrl="https://example.org/yell.mp3",
                durationSec=2400,
                publishedAt="2026-01-10",
                summary="Podcast episode on Yellowstone.",
                source="podcast",
            ),
            EpisodeRecord(
                id="nps-audio-1",
                show="National Park Service",
                title="Old Faithful Geyser Basin Audio Tour",
                category="Travel",
                audioUrl="https://www.nps.gov/av/yell/old-faithful.mp3",
                durationSec=420,
                publishedAt="2026-01-15",
                summary="Ranger audio tour.",
                source="nps",
            ),
        ],
        placesAndGear=[
            PlaceOrGearRecord(
                name="Old Faithful Geyser",
                type="Landmark",
                note="Iconic cone geyser.",
                episodeId="",
                timestampSec=0,
                show="",
                lat=match.lat,
                lon=match.lon,
                isUnesco=True,
                unescoId=_extract_unesco_id(match),
                unescoName=(
                    getattr(match, "name", None)
                    or getattr(match, "name_en", None)
                    or getattr(match, "unescoName", None)
                    or "Yellowstone National Park"
                ),
                source="nps",
            ),
            PlaceOrGearRecord(
                name="Lamar Valley",
                type="Landmark",
                note="Prime wildlife viewing at dawn.",
                episodeId="ep-pod-1",
                timestampSec=900,
                show="Zero To Travel",
                lat=44.8975,
                lon=-110.2211,
                source="podcast",
            ),
        ],
    )
    kml = build_city_kml(city)
    placemarks = {pm["name"]: pm for pm in _parse_kml_placemarks(kml)}
    assert "Old Faithful Geyser" in placemarks
    assert "[UNESCO World Heritage]" in placemarks["Old Faithful Geyser"]["description"]
    assert "(@ 00:00)" not in placemarks["Old Faithful Geyser"]["description"]

    assert "Lamar Valley" in placemarks
    assert "Zero To Travel @ 15:00" in placemarks["Lamar Valley"]["description"]


# ===========================================================================
# 5. Dual KML Parity: Python build_city_kml vs TypeScript buildCityKml
# ===========================================================================


def test_interaction_dual_kml_parity_python_vs_typescript() -> None:
    """Python build_city_kml and TS buildCityKml emit matching XML Placemarks, coords, and UNESCO/NPS format."""
    mixed_dict = make_mixed_city_dict()
    city = CityData.model_validate(mixed_dict)

    py_kml = build_city_kml(city)
    ts_out = _invoke_ts_export_pack(mixed_dict)
    ts_kml = ts_out["kml"]

    py_pms = _parse_kml_placemarks(py_kml)
    ts_pms = _parse_kml_placemarks(ts_kml)

    assert len(py_pms) == len(ts_pms) == 3, (
        f"Placemark count mismatch (Gear must be excluded): py={len(py_pms)}, ts={len(ts_pms)}"
    )
    assert [p["name"] for p in py_pms] == [p["name"] for p in ts_pms]
    assert [p["coordinates"] for p in py_pms] == [p["coordinates"] for p in ts_pms]

    for py_pm, ts_pm in zip(py_pms, ts_pms):
        if py_pm["name"] == "Kinkaku-ji":
            assert "[UNESCO World Heritage]" in py_pm["description"]
            assert "[UNESCO World Heritage]" in ts_pm["description"]
        if py_pm["name"] == "Cedar Trailhead Overlook":
            assert "(@ 00:00)" not in py_pm["description"]
            assert "(@ 00:00)" not in ts_pm["description"]


# ===========================================================================
# 6. TypeScript buildTripItineraryText on Mixed City
# ===========================================================================


def test_interaction_ts_itinerary_text_with_mixed_city() -> None:
    """TS buildTripItineraryText formats UNESCO, Wikidata cold-start books, and NPS places cleanly."""
    mixed_dict = make_mixed_city_dict()
    ts_out = _invoke_ts_export_pack(mixed_dict)
    itinerary = ts_out["itinerary"]

    assert "[UNESCO World Heritage]" in itinerary
    assert "The Old Capital" in itinerary
    assert "Cedar Trailhead Overlook" in itinerary
    # Cold-start Wikidata book and standalone NPS place must not show bogus "(@ 00:00)" or "( @ 00:00)"
    for line in itinerary.splitlines():
        if "The Old Capital" in line or "Cedar Trailhead Overlook" in line:
            assert "00:00" not in line, f"Unexpected 00:00 timestamp in itinerary line: {line!r}"


# ===========================================================================
# 7. Export -> Validate -> Load Round-Trip Preserving All Additive Fields
# ===========================================================================


def test_interaction_export_validate_load_roundtrip_preserves_additive_fields(tmp_path: Path) -> None:
    """export_static_site_data -> validate_output -> load_existing_cities preserves all F4 fields."""
    city = CityData.model_validate(make_mixed_city_dict())
    out_dir = tmp_path / "public"
    stats = export_static_site_data([city], out_dir)
    assert stats["cities"] == 1

    val_stats = validate_output(out_dir)
    assert val_stats["cities"] == 1

    reloaded = load_existing_cities(out_dir)
    assert "kyoto-japan" in reloaded
    rc = reloaded["kyoto-japan"]

    assert any(ep.source == "nps" for ep in rc.episodes)
    assert any(ep.source == "substack" for ep in rc.episodes)
    assert any(b.source == "wikidata" and b.wikidataId == "Q1209876" and b.episodeId == "" for b in rc.books)
    assert any(p.isUnesco is True and p.unescoId == "688" for p in rc.placesAndGear)
    assert any(p.source == "nps" and p.episodeId == "" for p in rc.placesAndGear)


# ===========================================================================
# 8. CLI check-feeds against Substack + Standard VTT RSS
# ===========================================================================


def test_interaction_check_feeds_cli_reports_substack_and_vtt(
    mock_http_server: LocalMockServer,
    tmp_path: Path,
) -> None:
    """podatlas-pipeline check-feeds reports both freeTranscriptEpisodes and withContentEncoded."""
    mock_http_server.add_route("/ep.vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nHello.\n", content_type="text/vtt")
    mock_http_server.add_route(
        "/sub.xml",
        make_substack_rss_xml(mock_http_server.base_url),
        content_type="application/rss+xml",
    )
    mock_http_server.add_route(
        "/vtt.xml",
        make_substack_rss_xml(
            mock_http_server.base_url,
            episode_guid="vtt-ep-1",
            content_html="",
            transcript_path="/ep.vtt",
        ),
        content_type="application/rss+xml",
    )
    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        f"sub-show,Substack Show,History,{mock_http_server.base_url}/sub.xml,,5\n"
        f"vtt-show,VTT Show,Travel,{mock_http_server.base_url}/vtt.xml,,5\n",
        encoding="utf-8",
    )
    proc = run_cli(
        ["check-feeds"],
        env_overrides={"PODATLAS_SHOWS_CSV": str(shows_csv)},
    )
    assert proc.returncode == 0, f"check-feeds failed: {proc.stderr}"
    report = json.loads(proc.stdout)
    assert report["feedsOk"] == 2
    assert report["freeTranscriptEpisodes"] >= 1
    details_by_slug = {d["slug"]: d for d in report["details"]}
    assert details_by_slug["sub-show"].get("withContentEncoded", 0) >= 1


# ===========================================================================
# 9. Static Export Produces All 6 Artifact Types Consistently
# ===========================================================================


def test_interaction_export_static_all_six_artifact_types_valid(tmp_path: Path) -> None:
    """export_static_site_data generates valid GeoJSON, JSON, KML, RSS, search-index, and sitemap."""
    city = CityData.model_validate(make_mixed_city_dict())
    out_dir = tmp_path / "public"
    export_static_site_data([city], out_dir)

    assert (out_dir / "cities.geojson").is_file()
    assert (out_dir / "data" / "kyoto-japan.json").is_file()
    assert (out_dir / "maps" / "kyoto-japan.kml").is_file()
    assert (out_dir / "feeds" / "kyoto-japan.xml").is_file()
    assert (out_dir / "search-index.json").is_file()
    assert (out_dir / "sitemap.xml").is_file()

    # Validate XML well-formedness for KML, RSS, Sitemap
    ET.fromstring((out_dir / "maps" / "kyoto-japan.kml").read_text(encoding="utf-8"))
    ET.fromstring((out_dir / "feeds" / "kyoto-japan.xml").read_text(encoding="utf-8"))
    ET.fromstring((out_dir / "sitemap.xml").read_text(encoding="utf-8"))


# ===========================================================================
# 10. NPS Public-Domain Audio Included in City RSS Feed
# ===========================================================================


def test_interaction_nps_episode_in_city_rss_feed_enclosure() -> None:
    """build_city_rss_xml includes NPS audio episode with its public-domain audio enclosure."""
    city = CityData.model_validate(make_mixed_city_dict())
    rss_xml = build_city_rss_xml(city, "https://podatlas.pages.dev")
    root = ET.fromstring(rss_xml)
    enclosures = [el.attrib.get("url", "") for el in root.findall(".//enclosure")]
    assert "https://www.nps.gov/av/cedar-groves.mp3" in enclosures


# ===========================================================================
# 11. Consensus Preserves Distinct Podcast UNESCO, Food, and NPS Places
# ===========================================================================


def test_interaction_consensus_preserves_mixed_source_places() -> None:
    """apply_city_consensus retains UNESCO landmark, Food spot, NPS landmark, and Gear distinctly."""
    city = CityData.model_validate(make_mixed_city_dict())
    merged = apply_city_consensus(city)
    by_name = {p.name: p for p in merged.placesAndGear}
    assert len(by_name) == 4
    assert by_name["Kinkaku-ji"].isUnesco is True
    assert by_name["Cedar Trailhead Overlook"].source == "nps"
    assert by_name["Cedar Trailhead Overlook"].episodeId == ""


# ===========================================================================
# 12. Feed Discovery Deduplication Against Scaled shows.csv
# ===========================================================================


def test_interaction_discover_feeds_dedupes_against_scaled_shows_csv() -> None:
    """discover_from_locations skips feeds already present in the 100+ shows.csv catalog."""
    shows = load_shows_csv(SHOWS_CSV)
    assert len(shows) >= 100
    sample_show = shows[0]
    existing_urls = {normalize_feed_url(s.rss_url) for s in shows}

    fake_records = [
        {
            "url": sample_show.rss_url.rstrip("/") + "/",
            "title": sample_show.title,
            "locale": "New York, USA",
            "country": "US",
            "id": 999991,
        },
        {
            "url": "https://brandnew-travel-pod.example.org/rss.xml",
            "title": "Brand New Travel Atlas",
            "locale": "Sydney, Australia",
            "country": "AU",
            "id": 999992,
        },
    ]
    discovered = discover_from_locations(fake_records, existing_urls, country_filter="", limit=10)
    urls = {normalize_feed_url(d.rssUrl) for d in discovered}
    assert normalize_feed_url(sample_show.rss_url) not in urls
    assert normalize_feed_url("https://brandnew-travel-pod.example.org/rss.xml") in urls
