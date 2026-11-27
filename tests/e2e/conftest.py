"""Shared fixtures and opaque-box test harnesses for PodAtlas E2E tests (Tiers 1–4)."""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import pytest

# ---------------------------------------------------------------------------
# Canonical repository path constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "pipeline"
WEB_DIR = REPO_ROOT / "web"
SHOWS_CSV = PIPELINE_DIR / "shows.csv"
WEB_PUBLIC_DIR = WEB_DIR / "public"
PIPELINE_CLI = PIPELINE_DIR / ".venv" / "bin" / "podatlas-pipeline"


# ---------------------------------------------------------------------------
# Lazy import & environment helpers (collection-safe, zero silent skips)
# ---------------------------------------------------------------------------


def import_planned_module(module_path: str) -> Any:
    """Lazily import a module inside a test body; fail cleanly if not yet implemented."""
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Required module '{module_path}' is not yet implemented: {exc}")


def require_web_node_modules() -> Path:
    """Assert web/node_modules is installed; fail with an actionable message if absent."""
    nm = WEB_DIR / "node_modules"
    if not nm.is_dir():
        pytest.fail(
            f"Frontend dependencies missing at {nm}. "
            "Run `npm install` in /home/antigravity/sthoms12/podatlas/web (M4)."
        )
    return nm


# ---------------------------------------------------------------------------
# Deterministic offline HTTP mock server (stdlib ThreadingHTTPServer)
# ---------------------------------------------------------------------------

RouteHandler = Callable[
    [str, dict[str, list[str]], dict[str, str], bytes],
    tuple[int, str, bytes | str | dict[str, Any]],
]


class LocalMockServer:
    """Ephemeral-port HTTP server serving deterministic offline responses."""

    def __init__(self) -> None:
        self._routes: dict[str, RouteHandler] = {}
        self._prefix_routes: list[tuple[str, RouteHandler]] = []
        self.requests_log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "Server not started"
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def add_route(
        self,
        path: str,
        body: bytes | str | dict[str, Any],
        *,
        status: int = 200,
        content_type: str | None = None,
    ) -> None:
        if content_type is None:
            content_type = "application/json" if isinstance(body, dict) else "text/plain; charset=utf-8"

        def _handler(
            _p: str, _q: dict[str, list[str]], _h: dict[str, str], _b: bytes
        ) -> tuple[int, str, bytes | str | dict[str, Any]]:
            return status, content_type, body

        self._routes[path] = _handler

    def add_dynamic_route(self, path: str, handler: RouteHandler, *, prefix: bool = False) -> None:
        if prefix:
            self._prefix_routes.append((path, handler))
        else:
            self._routes[path] = handler

    def _resolve(self, path: str) -> RouteHandler | None:
        if path in self._routes:
            return self._routes[path]
        for prefix, handler in self._prefix_routes:
            if path.startswith(prefix):
                return handler
        return None

    def start(self) -> "LocalMockServer":
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def _handle(self, method: str) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                length = int(self.headers.get("Content-Length", "0") or 0)
                body_bytes = self.rfile.read(length) if length > 0 else b""
                headers_dict = {k.lower(): v for k, v in self.headers.items()}

                with outer._lock:
                    outer.requests_log.append(
                        {
                            "method": method,
                            "path": parsed.path,
                            "query": query,
                            "headers": headers_dict,
                            "body": body_bytes,
                        }
                    )

                handler = outer._resolve(parsed.path)
                if handler is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"Not Found")
                    return

                status, content_type, payload = handler(parsed.path, query, headers_dict, body_bytes)
                if isinstance(payload, dict):
                    raw = json.dumps(payload).encode("utf-8")
                elif isinstance(payload, str):
                    raw = payload.encode("utf-8")
                else:
                    raw = payload

                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


@pytest.fixture
def mock_http_server():
    server = LocalMockServer().start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Subprocess CLI helper with isolated PODATLAS_* environment
# ---------------------------------------------------------------------------


def run_cli(
    args: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    """Run `podatlas-pipeline` in an isolated subprocess without ambient PODATLAS_*/NPS_API_KEY."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PODATLAS_") and k != "NPS_API_KEY"
    }
    if env_overrides:
        env.update(env_overrides)

    return subprocess.run(
        [str(PIPELINE_CLI), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# Deterministic offline fixture builders
# ---------------------------------------------------------------------------


LONG_SUBSTACK_ESSAY_HTML = """
<style>.paywall { display: none; }</style>
<script>console.log('tracking pixel');</script>
<p>Welcome to our walking tour of Kyoto &amp; its historic wooden temples.
We begin at Kinkaku-ji, the Golden Pavilion, set beside its mirror pond in northern Kyoto.</p>
<p>After visiting the pavilion, we stop at Nishiki Market for matcha sweets and pickled vegetables.
For reading on the Shinkansen, I strongly recommend &ldquo;The Temple of the Golden Pavilion&rdquo;
by Yukio Mishima, which captures the atmospheric tension of postwar Kyoto.</p>
<p>In the afternoon, walk south through Higashiyama to Kiyomizu-dera, perched high above the maple
valley. Bring comfortable walking shoes and a compact umbrella because afternoon showers sweep
across the hills throughout spring and autumn.</p>
<p>Evenings along the Kamogawa River reward slow wandering, small izakaya counters, and quiet
conversations about centuries of Japanese architecture, craft, and culinary tradition.</p>
""".strip()


def make_substack_rss_xml(
    base_url: str,
    *,
    episode_guid: str = "substack-kyoto-ep-1",
    episode_title: str = "Walking Historic Kyoto & Golden Pavilions",
    audio_path: str = "/audio/kyoto-ep1.m4a?utm_source=substack&token=xyz123",
    audio_mime: str = "audio/x-m4a",
    duration: str = "00:42:00",
    content_html: str = LONG_SUBSTACK_ESSAY_HTML,
    transcript_path: str | None = None,
    subject_location: str | None = "Kyoto, Japan",
) -> str:
    """Build a valid RSS 2.0 XML document simulating an audio-enabled Substack feed."""
    transcript_tag = (
        f'<podcast:transcript url="{base_url}{transcript_path}" type="text/vtt" />'
        if transcript_path
        else ""
    )
    location_tag = (
        f'<podcast:location rel="subject">{subject_location}</podcast:location>'
        if subject_location
        else ""
    )
    enclosure_tag = (
        f'<enclosure url="{base_url}{audio_path}" length="12345678" type="{audio_mime}" />'
        if audio_path
        else ""
    )
    content_block = f"<content:encoded><![CDATA[{content_html}]]></content:encoded>" if content_html else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Slow Kyoto Dispatch</title>
    <link>{base_url}/feed</link>
    <description>Audio essays on Japanese travel, architecture, and food.</description>
    <item>
      <guid isPermaLink="false">{episode_guid}</guid>
      <title>{episode_title}</title>
      <pubDate>Tue, 10 Feb 2026 08:00:00 GMT</pubDate>
      <itunes:duration>{duration}</itunes:duration>
      <description>Short teaser about Kyoto temples.</description>
      {enclosure_tag}
      {transcript_tag}
      {location_tag}
      {content_block}
    </item>
  </channel>
</rss>
""".strip()


def write_geonames_fixture(cache_dir: Path) -> Path:
    """Write a deterministic GeoNames cities5000.txt subset with real GeoNames IDs."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        # geonameid, name, asciiname, alternatenames, lat, lon, fclass, fcode, cc, cc2, a1, a2, a3, a4, pop, elev, dem, tz, mod
        "1857910\tKyoto\tKyoto\tKyoto,Kioto\t35.02107\t135.75385\tP\tPPLA\tJP\t\t22\t\t\t\t1467785\t\t50\tAsia/Tokyo\t2024-01-01",
        "3169070\tRome\tRome\tRoma,Rome\t41.89193\t12.51133\tP\tPPLC\tIT\t\t07\t\t\t\t2318895\t\t21\tEurope/Rome\t2024-01-01",
        "3413829\tReykjavik\tReykjavik\tReykjavik,Reykjavík\t64.13548\t-21.89541\tP\tPPLC\tIS\t\t10\t\t\t\t118918\t\t14\tAtlantic/Reykjavik\t2024-01-01",
        "3941584\tCusco\tCusco\tCuzco,Cusco\t-13.52264\t-71.96734\tP\tPPLA\tPE\t\t08\t\t\t\t428450\t\t3400\tAmerica/Lima\t2024-01-01",
        "2988507\tParis\tParis\tParis,Lutetia\t48.85341\t2.34880\tP\tPPLC\tFR\t\t11\t\t\t\t2138551\t\t42\tEurope/Paris\t2024-01-01",
        "5653693\tGardiner\tGardiner\tGardiner\t45.03188\t-110.70577\tP\tPPL\tUS\t\tMT\t\t\t\t8750\t\t1600\tAmerica/Denver\t2024-01-01",
    ]
    txt_path = cache_dir / "cities5000.txt"
    txt_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return txt_path


def make_unesco_whc001_geojson() -> dict[str, Any]:
    """Build a realistic UNESCO whc001 Open GeoJSON FeatureCollection fixture."""
    kyoto_components = json.dumps(
        [
            {"name": "Kinkaku-ji (Rokuon-ji)", "lat": 35.0394, "lon": 135.7292},
            {"name": "Kiyomizu-dera", "lat": 34.9949, "lon": 135.7850},
            {"name": "Nijo-jo Castle", "lat": 35.0142, "lon": 135.7482},
        ]
    )
    rome_components = json.dumps(
        [
            {"name": "Colosseum", "lat": 41.8902, "lon": 12.4922},
            {"name": "Roman Forum", "lat": 41.8925, "lon": 12.4853},
        ]
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [135.7292, 35.0394]},
                "properties": {
                    "id_no": "688",
                    "name_en": "Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)",
                    "states_name_en": ["Japan"],
                    "category": "Cultural",
                    "geo_point_2d": {"lon": 135.7292, "lat": 35.0394},
                    "components_list": kyoto_components,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [12.4922, 41.8902]},
                "properties": {
                    "id_no": "91",
                    "name_en": "Historic Centre of Rome",
                    "states_name_en": ["Italy"],
                    "category": "Cultural",
                    "geo_point_2d": {"lon": 12.4922, "lat": 41.8902},
                    "components_list": rome_components,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-110.5885, 44.4280]},
                "properties": {
                    "id_no": "28",
                    "name_en": "Yellowstone National Park",
                    "states_name_en": ["United States of America"],
                    "category": "Natural",
                    "geo_point_2d": {"lon": -110.5885, "lat": 44.4280},
                    "components_list": json.dumps(
                        [{"name": "Old Faithful Geyser", "lat": 44.4605, "lon": -110.8281}]
                    ),
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[-72.5450, -13.1631], [-72.5440, -13.1620]],
                },
                "properties": {
                    "id_no": "274",
                    "name_en": "Historic Sanctuary of Machu Picchu",
                    "states_name_en": ["Peru"],
                    "category": "Mixed",
                    "components_list": "NOT_VALID_JSON",
                },
            },
        ],
    }


def write_unesco_fixture(cache_dir: Path, filename: str = "unesco_whc001.geojson") -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / filename
    path.write_text(json.dumps(make_unesco_whc001_geojson()), encoding="utf-8")
    return path


def make_mixed_city_dict() -> dict[str, Any]:
    """Return a rich mixed CityData payload with podcast, UNESCO, NPS, and Wikidata items."""
    return {
        "id": "kyoto-japan",
        "name": "Kyoto",
        "country": "Japan",
        "lat": 35.0211,
        "lon": 135.7539,
        "primaryCategory": "History",
        "tagline": "Ancient temples, cedar alleys, and living craft traditions.",
        "episodes": [
            {
                "id": "ep-kyoto-1",
                "show": "Slow Kyoto Dispatch",
                "title": "Walking Historic Kyoto & Golden Pavilions",
                "category": "History",
                "audioUrl": "https://example.org/audio/kyoto-1.m4a",
                "durationSec": 2520,
                "publishedAt": "2026-02-10",
                "summary": "A walking essay through northern Kyoto temples and Nishiki Market.",
                "mentions": [
                    {"sec": 600, "type": "place", "label": "Kinkaku-ji"},
                    {"sec": 900, "type": "book", "label": "The Temple of the Golden Pavilion"},
                ],
                "source": "substack",
            },
            {
                "id": "nps-audio-1",
                "show": "National Park Service",
                "title": "Ranger Walk: Cedar Groves",
                "category": "Travel",
                "audioUrl": "https://www.nps.gov/av/cedar-groves.mp3",
                "durationSec": 480,
                "publishedAt": "2026-01-15",
                "summary": "Official ranger audio stop on historic cedar preservation.",
                "mentions": [],
                "source": "nps",
            },
        ],
        "books": [
            {
                "title": "The Temple of the Golden Pavilion",
                "author": "Yukio Mishima",
                "isbn": "9780679752707",
                "openLibraryKey": "/works/OL12345W",
                "wikidataId": "Q1197128",
                "episodeId": "ep-kyoto-1",
                "show": "Slow Kyoto Dispatch",
                "timestampSec": 900,
                "quote": "Captures the atmospheric tension of postwar Kyoto.",
                "endorsement": "strong_rec",
                "mentionCount": 2,
                "uniqueShowCount": 2,
                "mentionedByShows": ["Slow Kyoto Dispatch", "Japan Station"],
                "source": "podcast",
            },
            {
                "title": "The Old Capital",
                "author": "Yasunari Kawabata",
                "isbn": "9781593760328",
                "openLibraryKey": "/works/OL99887W",
                "wikidataId": "Q1209876",
                "episodeId": "",
                "show": "",
                "timestampSec": 0,
                "quote": "",
                "endorsement": "strong_rec",
                "mentionCount": 1,
                "uniqueShowCount": 1,
                "mentionedByShows": [],
                "source": "wikidata",
            },
        ],
        "placesAndGear": [
            {
                "name": "Kinkaku-ji",
                "type": "Landmark",
                "note": "Golden Pavilion beside its mirror pond.",
                "episodeId": "ep-kyoto-1",
                "timestampSec": 600,
                "show": "Slow Kyoto Dispatch",
                "lat": 35.0394,
                "lon": 135.7292,
                "endorsement": "strong_rec",
                "mentionCount": 2,
                "uniqueShowCount": 2,
                "mentionedByShows": ["Slow Kyoto Dispatch", "Japan Station"],
                "isUnesco": True,
                "unescoId": "688",
                "unescoName": "Historic Monuments of Ancient Kyoto (Kyoto, Uji and Otsu Cities)",
                "source": "podcast",
            },
            {
                "name": "Nishiki Market",
                "type": "Food",
                "note": "Historic covered market for matcha sweets and pickles.",
                "episodeId": "ep-kyoto-1",
                "timestampSec": 750,
                "show": "Slow Kyoto Dispatch",
                "lat": 35.0050,
                "lon": 135.7649,
                "endorsement": "strong_rec",
                "mentionCount": 1,
                "uniqueShowCount": 1,
                "mentionedByShows": ["Slow Kyoto Dispatch"],
                "isUnesco": False,
                "source": "podcast",
            },
            {
                "name": "Cedar Trailhead Overlook",
                "type": "Landmark",
                "note": "Official park overlook with interpretive signage.",
                "episodeId": "",
                "timestampSec": 0,
                "show": "",
                "lat": 35.0116,
                "lon": 135.7681,
                "endorsement": "strong_rec",
                "mentionCount": 1,
                "uniqueShowCount": 1,
                "mentionedByShows": [],
                "isUnesco": False,
                "source": "nps",
            },
            {
                "name": "Compact Travel Umbrella",
                "type": "Gear",
                "note": "Essential for sudden Kyoto afternoon showers.",
                "episodeId": "ep-kyoto-1",
                "timestampSec": 1100,
                "show": "Slow Kyoto Dispatch",
                "lat": 35.0116,
                "lon": 135.7681,
                "endorsement": "strong_rec",
                "mentionCount": 1,
                "uniqueShowCount": 1,
                "mentionedByShows": ["Slow Kyoto Dispatch"],
                "isUnesco": False,
                "source": "podcast",
            },
        ],
    }


def make_nps_parks_payload() -> dict[str, Any]:
    return {
        "total": "1",
        "data": [
            {
                "id": "park-yell-1",
                "parkCode": "yell",
                "fullName": "Yellowstone National Park",
                "name": "Yellowstone",
                "states": "ID,MT,WY",
                "latitude": "44.59824417",
                "longitude": "-110.5471695",
                "description": "America's first national park, home to geysers and alpine wildlife.",
                "url": "https://www.nps.gov/yell/index.htm",
            }
        ],
    }


def make_nps_audio_payload() -> dict[str, Any]:
    return {
        "total": "1",
        "data": [
            {
                "id": "audio-yell-1",
                "title": "Old Faithful Geyser Basin Audio Tour",
                "description": "Ranger-led walk explaining geothermal eruptions.",
                "durationMs": 420000,
                "versions": [
                    {
                        "url": "https://www.nps.gov/av/yell/old-faithful.mp3",
                        "fileType": "mp3",
                    }
                ],
                "relatedParks": [{"parkCode": "yell", "fullName": "Yellowstone National Park"}],
            }
        ],
    }


def make_nps_places_payload() -> dict[str, Any]:
    return {
        "total": "2",
        "data": [
            {
                "id": "place-yell-1",
                "title": "Old Faithful Geyser",
                "listingDescription": "Iconic cone geyser erupting every 60 to 110 minutes.",
                "latitude": "44.4605",
                "longitude": "-110.8281",
                "relatedParks": [{"parkCode": "yell"}],
            },
            {
                "id": "place-yell-no-coords",
                "title": "Backcountry Permit Office",
                "listingDescription": "Visitor desk without coordinates.",
                "latitude": "",
                "longitude": "",
                "relatedParks": [{"parkCode": "yell"}],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Resilient Wikidata SPARQL & M2/M3 Client Adapter Helpers
# ---------------------------------------------------------------------------

_SELECT_VAR_RE = re.compile(r"\?([A-Za-z_][A-Za-z0-9_]*)")


def make_sparql_binding(
    qid: str,
    title: str,
    author: str = "",
    *,
    open_library_id: str | None = None,
    isbn13: str | None = None,
) -> dict[str, dict[str, str]]:
    """Build a Wikidata SPARQL binding row with standard variable-name aliases.

    Populates common alias keys (book/work/item/s, bookLabel/workLabel/itemLabel/title,
    authorLabel/author, openLibraryId/olId/olKey/openLibrary, isbn13/isbn) so any valid
    M2 SPARQL query naming works cleanly.
    """
    entity_uri = qid if qid.startswith("http") else f"http://www.wikidata.org/entity/{qid}"
    uri_val = {"type": "uri", "value": entity_uri}
    title_val = {"type": "literal", "value": title}

    row: dict[str, dict[str, str]] = {
        "book": uri_val,
        "work": uri_val,
        "item": uri_val,
        "s": uri_val,
        "bookLabel": title_val,
        "workLabel": title_val,
        "itemLabel": title_val,
        "sLabel": title_val,
        "title": title_val,
    }
    if author:
        author_val = {"type": "literal", "value": author}
        row["authorLabel"] = author_val
        row["author"] = author_val
    if open_library_id:
        ol_val = {"type": "literal", "value": open_library_id}
        row["openLibraryId"] = ol_val
        row["olId"] = ol_val
        row["olKey"] = ol_val
        row["openLibrary"] = ol_val
    if isbn13:
        isbn_val = {"type": "literal", "value": isbn13}
        row["isbn13"] = isbn_val
        row["isbn"] = isbn_val
    return row


def _extract_select_vars(sparql_query: str) -> list[str]:
    """Extract ?var names from the SELECT clause of a SPARQL query."""
    m = re.search(r"SELECT\b(.*?)\bWHERE\b", sparql_query, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    return _SELECT_VAR_RE.findall(m.group(1))


def add_wikidata_sparql_route(
    server: LocalMockServer,
    bindings: list[dict[str, dict[str, str]]],
    *,
    path: str = "/sparql",
) -> None:
    """Register a dynamic /sparql route that serves bindings and adapts to SELECT ?var names."""

    def _handler(
        _p: str, query_params: dict[str, list[str]], _h: dict[str, str], body: bytes
    ) -> tuple[int, str, dict[str, Any]]:
        sparql_text = ""
        if "query" in query_params and query_params["query"]:
            sparql_text = query_params["query"][0]
        elif body:
            body_str = body.decode("utf-8", errors="ignore")
            parsed_body = parse_qs(body_str)
            sparql_text = parsed_body.get("query", [body_str])[0]

        select_vars = _extract_select_vars(sparql_text)
        adapted_bindings: list[dict[str, dict[str, str]]] = []
        for orig_row in bindings:
            row = dict(orig_row)
            for var in select_vars:
                if var in row:
                    continue
                low = var.lower()
                if "isbn" in low and "isbn13" in row:
                    row[var] = row["isbn13"]
                elif ("ol" in low or "openlibrary" in low or "open_library" in low) and "openLibraryId" in row:
                    row[var] = row["openLibraryId"]
                elif ("author" in low or "writer" in low or "creator" in low) and "authorLabel" in row:
                    row[var] = row["authorLabel"]
                elif ("label" in low or "title" in low or "name" in low) and "bookLabel" in row:
                    row[var] = row["bookLabel"]
                elif any(tok in low for tok in ("book", "work", "item", "novel", "entity", "qid")) and "book" in row:
                    row[var] = row["book"]
            adapted_bindings.append(row)

        return 200, "application/sparql-results+json", {"results": {"bindings": adapted_bindings}}

    server.add_dynamic_route(path, _handler)


def make_wikidata_client(
    wd_mod: Any,
    base_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Construct WikidataBookClient pointing strictly at the local mock server."""
    WikidataBookClient = getattr(wd_mod, "WikidataBookClient")
    sparql_url = f"{base_url}/sparql"
    api_url = f"{base_url}/w/api.php"

    for attr_name in ("WIKIDATA_SPARQL_URL", "SPARQL_ENDPOINT", "SPARQL_URL"):
        if hasattr(wd_mod, attr_name):
            monkeypatch.setattr(wd_mod, attr_name, sparql_url)
    for attr_name in ("WIKIDATA_API_URL", "API_URL"):
        if hasattr(wd_mod, attr_name):
            monkeypatch.setattr(wd_mod, attr_name, api_url)

    sig = inspect.signature(WikidataBookClient.__init__)
    kwargs: dict[str, Any] = {}
    if "cache_path" in sig.parameters:
        kwargs["cache_path"] = tmp_path / "wd_cache.json"
    elif "cache_dir" in sig.parameters:
        kwargs["cache_dir"] = tmp_path / "wd_cache"
    if "sparql_url" in sig.parameters:
        kwargs["sparql_url"] = sparql_url
    elif "sparql_endpoint" in sig.parameters:
        kwargs["sparql_endpoint"] = sparql_url
    if "api_url" in sig.parameters:
        kwargs["api_url"] = api_url

    client = WikidataBookClient(**kwargs)
    for attr_name in ("sparql_url", "sparql_endpoint", "SPARQL_URL", "SPARQL_ENDPOINT"):
        if hasattr(client, attr_name):
            setattr(client, attr_name, sparql_url)
    for attr_name in ("api_url", "API_URL"):
        if hasattr(client, attr_name):
            setattr(client, attr_name, api_url)
    return client


def get_wikidata_city_books_fn(client: Any) -> Callable[..., Any]:
    """Discover WikidataBookClient's cold-start city books query method, failing explicitly if absent."""
    candidate_names = (
        "fetch_books_for_city",
        "books_for_city",
        "fetch_city_books",
        "get_city_books",
        "fill_cold_start_books",
        "query_city_books",
        "find_city_books",
        "get_books_for_geonames_id",
        "find_books_set_in_city",
    )
    for name in candidate_names:
        fn = getattr(client, name, None)
        if callable(fn):
            return fn
    pytest.fail(
        f"WikidataBookClient does not expose any recognized city cold-start book query method "
        f"(checked: {', '.join(candidate_names)})"
    )


def get_wikidata_verify_fn(client: Any) -> Callable[..., Any]:
    """Discover WikidataBookClient's single-book verification method, failing explicitly if absent."""
    candidate_names = ("verify", "verify_book", "lookup_book", "find_book", "search_book")
    for name in candidate_names:
        fn = getattr(client, name, None)
        if callable(fn):
            return fn
    pytest.fail(
        f"WikidataBookClient does not expose any recognized book verification method "
        f"(checked: {', '.join(candidate_names)})"
    )


def make_nps_client(
    nps_mod: Any,
    api_key: str,
    base_url: str,
    cache_dir: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Any:
    """Construct NPSClient pointing strictly at the local mock server (never live developer.nps.gov)."""
    NPSClient = getattr(nps_mod, "NPSClient")

    for attr_name in ("NPS_API_BASE", "NPS_BASE_URL", "BASE_URL", "API_BASE", "NPS_API_URL"):
        if hasattr(nps_mod, attr_name):
            if monkeypatch is not None:
                monkeypatch.setattr(nps_mod, attr_name, base_url)
            else:
                setattr(nps_mod, attr_name, base_url)

    sig = inspect.signature(NPSClient.__init__)
    kwargs: dict[str, Any] = {"api_key": api_key}
    if "cache_dir" in sig.parameters:
        kwargs["cache_dir"] = cache_dir
    elif "cache_path" in sig.parameters:
        kwargs["cache_path"] = cache_dir
    if "base_url" in sig.parameters:
        kwargs["base_url"] = base_url
    elif "api_base" in sig.parameters:
        kwargs["api_base"] = base_url

    client = NPSClient(**kwargs)
    for attr_name in ("base_url", "BASE_URL", "api_base", "API_BASE"):
        if hasattr(client, attr_name):
            setattr(client, attr_name, base_url)
    return client


def call_nps_parks(
    client: Any,
    *,
    park_code: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Call NPSClient's parks listing method across supported method names."""
    candidate_names = ("fetch_parks", "get_parks", "list_parks")
    fn = None
    for name in candidate_names:
        cand = getattr(client, name, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        pytest.fail(
            f"NPSClient does not expose any recognized parks method (checked: {', '.join(candidate_names)})"
        )

    params = inspect.signature(fn).parameters
    kwargs: dict[str, Any] = {}
    if park_code is not None and "park_code" in params:
        kwargs["park_code"] = park_code
    if limit is not None and "limit" in params:
        kwargs["limit"] = limit
    return fn(**kwargs)


def call_nps_park_builder(
    nps_mod: Any,
    client: Any,
    parks_payload: dict[str, Any],
    audio_payload: dict[str, Any],
    places_payload: dict[str, Any],
    *,
    park_code: str = "yell",
    limit: int = 1,
) -> list[Any]:
    """Invoke nps.py's park->CityData builder or ingestion helper across supported public names."""
    client_builders = ("ingest_nps_parks", "fetch_and_build_parks", "ingest_parks")
    for name in client_builders:
        fn = getattr(nps_mod, name, None)
        if callable(fn):
            params = inspect.signature(fn).parameters
            kwargs: dict[str, Any] = {}
            if "park_code" in params:
                kwargs["park_code"] = park_code
            if "limit" in params:
                kwargs["limit"] = limit
            res = fn(client, **kwargs)
            return list(res.values()) if isinstance(res, dict) else list(res)

    payload_builders = (
        "build_park_city_data",
        "park_to_city_data",
        "build_city_from_park",
        "map_park_to_city_data",
        "convert_park_to_city",
    )
    for name in payload_builders:
        fn = getattr(nps_mod, name, None)
        if callable(fn):
            park = parks_payload["data"][0]
            audio = audio_payload["data"]
            places = places_payload["data"]
            return [fn(park, audio, places)]

    pytest.fail(
        "nps.py must expose a public park->CityData builder or ingestion function "
        f"(checked: {', '.join(client_builders + payload_builders)})"
    )

