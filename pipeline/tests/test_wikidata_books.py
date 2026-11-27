"""Offline unit tests for Wikidata P840 book verification, cold-start filling, and consensus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from podatlas_pipeline.export_static import apply_city_consensus, build_search_index
from podatlas_pipeline.geocode import GeoNamesIndex
from podatlas_pipeline.models import BookRecord, CityData, EpisodeRecord
from podatlas_pipeline.verify_books import OpenLibraryVerifier, VerifiedBook
from podatlas_pipeline.wikidata_books import (
    WikidataBookClient,
    fill_cold_start_books,
    normalize_open_library_id,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def test_geonames_file_populates_geonames_id(tmp_path: Path):
    sample = "\n".join(
        [
            "1857910\tKyoto\tKyoto\tKioto\t35.02107\t135.75385\tP\tPPLA\tJP\t\t22\t\t\t\t1467785",
            "2267057\tLisbon\tLisbon\tLisboa\t38.71667\t-9.13333\tP\tPPLC\tPT\t\t14\t\t\t\t517802",
        ]
    )
    txt = tmp_path / "cities5000.txt"
    txt.write_text(sample, encoding="utf-8")

    idx = GeoNamesIndex.from_geonames_file(txt)
    kyoto = idx.lookup("Kyoto", "Japan")
    lisbon = idx.lookup("Lisbon", "Portugal")
    assert kyoto is not None and kyoto.geonames_id == "1857910"
    assert lisbon is not None and lisbon.geonames_id == "2267057"


def test_normalize_open_library_id():
    assert normalize_open_library_id("OL12345W") == "/works/OL12345W"
    assert normalize_open_library_id("OL99999M") == "/books/OL99999M"
    assert normalize_open_library_id("/works/OL12345W") == "/works/OL12345W"
    assert normalize_open_library_id("/books/OL99999M") == "/books/OL99999M"
    assert normalize_open_library_id("") is None
    assert normalize_open_library_id(None) is None


def test_open_library_falls_back_to_wikidata_on_miss_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def _mock_get(self, url, params=None, headers=None):
        params = params or {}
        if "openlibrary.org" in str(url):
            # Simulate Open Library returning no docs
            return _FakeResponse({"docs": []})
        if "wikidata.org/w/api.php" in str(url):
            action = params.get("action")
            if action == "wbsearchentities":
                return _FakeResponse(
                    {
                        "search": [
                            {"id": "Q1145005", "label": "The Temple of the Golden Pavilion"}
                        ]
                    }
                )
            if action == "wbgetentities" and params.get("ids") == "Q1145005":
                return _FakeResponse(
                    {
                        "entities": {
                            "Q1145005": {
                                "labels": {"en": {"value": "The Temple of the Golden Pavilion"}},
                                "aliases": {"en": [{"value": "Kinkaku-ji"}]},
                                "claims": {
                                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7725634"}}}}],
                                    "P50": [{"mainsnak": {"datavalue": {"value": {"id": "Q180736"}}}}],
                                    "P212": [{"mainsnak": {"datavalue": {"value": "978-0-679-75270-7"}}}],
                                    "P648": [{"mainsnak": {"datavalue": {"value": "OL1516543W"}}}],
                                },
                            }
                        }
                    }
                )
            if action == "wbgetentities" and params.get("ids") == "Q180736":
                return _FakeResponse(
                    {
                        "entities": {
                            "Q180736": {
                                "labels": {"en": {"value": "Yukio Mishima"}},
                            }
                        }
                    }
                )
        raise AssertionError(f"Unexpected HTTP call: {url} {params}")

    monkeypatch.setattr(httpx.Client, "get", _mock_get)

    wd_client = WikidataBookClient(cache_path=tmp_path / "wd_cache.json")
    ol_verifier = OpenLibraryVerifier(
        cache_path=tmp_path / "ol_cache.json",
        fallback_verifier=wd_client,
    )

    book = ol_verifier.verify("The Temple of the Golden Pavilion", "Yukio Mishima")
    assert book is not None
    assert book.title == "The Temple of the Golden Pavilion"
    assert book.author == "Yukio Mishima"
    assert book.isbn == "9780679752707"
    assert book.open_library_key == "/works/OL1516543W"
    assert book.wikidata_id == "Q1145005"

    # Also verify fallback triggers when Open Library raises a network error
    def _mock_ol_error(self, url, params=None, headers=None):
        if "openlibrary.org" in str(url):
            raise httpx.ConnectTimeout("OL timeout")
        return _mock_get(self, url, params=params, headers=headers)

    monkeypatch.setattr(httpx.Client, "get", _mock_ol_error)
    ol_verifier2 = OpenLibraryVerifier(
        cache_path=tmp_path / "ol_cache2.json",
        fallback_verifier=wd_client,
    )
    book2 = ol_verifier2.verify("The Temple of the Golden Pavilion", "Yukio Mishima")
    assert book2 is not None
    assert book2.wikidata_id == "Q1145005"


def test_wikidata_sparql_city_books_dedupe_qid_filter_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured_queries: list[str] = []

    def _mock_sparql_get(self, url, params=None, headers=None):
        assert "query.wikidata.org/sparql" in str(url)
        captured_queries.append(str((params or {}).get("query", "")))
        return _FakeResponse(
            {
                "results": {
                    "bindings": [
                        # Duplicate rows for Q1145005 with ISBN-10 then ISBN-13 and edition then work OL ID
                        {
                            "book": {"value": "http://www.wikidata.org/entity/Q1145005"},
                            "bookLabel": {"value": "The Temple of the Golden Pavilion"},
                            "authorLabel": {"value": "Yukio Mishima"},
                            "isbn10": {"value": "0-679-75270-6"},
                            "olId": {"value": "OL77777M"},
                        },
                        {
                            "book": {"value": "http://www.wikidata.org/entity/Q1145005"},
                            "bookLabel": {"value": "The Temple of the Golden Pavilion"},
                            "authorLabel": {"value": "Yukio Mishima"},
                            "isbn13": {"value": "978-0-679-75270-7"},
                            "olId": {"value": "OL1516543W"},
                        },
                        # Raw Q-ID title MUST be filtered out
                        {
                            "book": {"value": "http://www.wikidata.org/entity/Q999999"},
                            "bookLabel": {"value": "Q999999"},
                            "authorLabel": {"value": "Some Author"},
                        },
                        # Raw Q-ID author label ignored -> falls back to Unknown Author
                        {
                            "book": {"value": "http://www.wikidata.org/entity/Q123456"},
                            "bookLabel": {"value": "The Old Capital"},
                            "authorLabel": {"value": "Q888888"},
                            "olId": {"value": "OL22222W"},
                        },
                    ]
                }
            }
        )

    monkeypatch.setattr(httpx.Client, "get", _mock_sparql_get)

    client = WikidataBookClient(cache_path=tmp_path / "wd_sparql.json")
    books = client.fetch_books_for_city("Kyoto", "Japan", geonames_id="1857910", limit=5)
    assert len(books) == 2
    assert 'wdt:P1566 "1857910"' in captured_queries[0]

    assert books[0].wikidata_id == "Q1145005"
    assert books[0].isbn == "9780679752707"  # ISBN-13 preferred over ISBN-10
    assert books[0].open_library_key == "/works/OL1516543W"  # Work key preferred & normalized

    assert books[1].wikidata_id == "Q123456"
    assert books[1].title == "The Old Capital"
    assert books[1].author == "Unknown Author"
    assert books[1].open_library_key == "/works/OL22222W"

    # Label + country fallback when geonames_id is empty
    books_fallback = client.fetch_books_for_city("Lisbon", "Portugal", geonames_id="", limit=2)
    assert len(books_fallback) == 2
    assert 'rdfs:label "Lisbon"@en' in captured_queries[1]
    assert 'rdfs:label "Portugal"@en' in captured_queries[1]


def test_fill_cold_start_books_respects_thresholds_and_dedupe():
    class FakeWikidataClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def fetch_books_for_city(self, city_name, country, geonames_id="", limit=5):
            self.calls.append((city_name, geonames_id))
            return [
                VerifiedBook(
                    title="Existing Book",
                    author="Jane Doe",
                    isbn="9780000000001",
                    open_library_key="/works/OL1W",
                    wikidata_id="Q1",
                ),
                VerifiedBook(
                    title="Cold Start Novel 1",
                    author="Author One",
                    isbn="9780000000002",
                    open_library_key="/works/OL2W",
                    wikidata_id="Q2",
                ),
                VerifiedBook(
                    title="Cold Start Novel 2",
                    author="Author Two",
                    isbn=None,
                    open_library_key=None,
                    wikidata_id="Q3",
                ),
                VerifiedBook(
                    title="Cold Start Novel 3",
                    author="Author Three",
                    isbn=None,
                    open_library_key=None,
                    wikidata_id="Q4",
                ),
            ]

    cities = {
        "kyoto-japan": CityData(
            id="kyoto-japan",
            name="Kyoto",
            country="Japan",
            lat=35.0116,
            lon=135.7681,
            primaryCategory="History",
            tagline="Imperial capital.",
            books=[
                BookRecord(
                    title="Existing Book",
                    author="Jane Doe",
                    isbn="9780000000001",
                    episodeId="ep-1",
                    show="Show A",
                )
            ],
        ),
        "rich-city": CityData(
            id="rich-city",
            name="Rich City",
            country="France",
            lat=48.85,
            lon=2.35,
            primaryCategory="Culture",
            tagline="Lots of books.",
            books=[
                BookRecord(title=f"Book {i}", author="Auth", episodeId="ep-1", show="Show A")
                for i in range(3)
            ],
        ),
    }

    fake_wd = FakeWikidataClient()
    added = fill_cold_start_books(
        cities,
        fake_wd,  # type: ignore[arg-type]
        min_books=3,
        max_add=2,
    )
    # Only Kyoto needed filling; Existing Book skipped; capped at max_add=2
    assert added == 2
    assert len(fake_wd.calls) == 1
    assert fake_wd.calls[0][0] == "Kyoto"
    assert len(cities["kyoto-japan"].books) == 3
    wd_books = [b for b in cities["kyoto-japan"].books if b.source == "wikidata"]
    assert len(wd_books) == 2
    assert all(b.episodeId == "" and b.show == "" and b.mentionedByShows == [] for b in wd_books)


def test_consensus_podcast_precedence_and_search_index_formatting():
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
                show="History of Japan Podcast",
                title="Ep 1",
                category="History",
                durationSec=1800,
                publishedAt="2026-09-01",
                summary="Summary",
            )
        ],
        books=[
            # Wikidata book listed BEFORE podcast book, matching by title+author (different IDs)
            BookRecord(
                title="The Temple of the Golden Pavilion",
                author="Yukio Mishima",
                wikidataId="Q1145005",
                source="wikidata",
                episodeId="",
                show="",
                timestampSec=0,
                quote="Set in Kyoto (Wikidata narrative location).",
                endorsement="casual_mention",
                mentionedByShows=[],
            ),
            BookRecord(
                title="The Temple of the Golden Pavilion",
                author="Yukio Mishima",
                isbn="9780679752707",
                source="podcast",
                episodeId="ep-1",
                show="History of Japan Podcast",
                timestampSec=420,
                quote="Essential Kyoto novel.",
                endorsement="strong_rec",
                mentionedByShows=["History of Japan Podcast"],
            ),
            BookRecord(
                title="A Cold Start Classic",
                author="Famous Author",
                wikidataId="Q55555",
                source="wikidata",
                episodeId="",
                show="",
                timestampSec=0,
                quote="Set in Kyoto (Wikidata narrative location).",
                endorsement="casual_mention",
                mentionedByShows=[],
            ),
        ],
    )

    merged = apply_city_consensus(city)
    assert len(merged.books) == 2

    # Podcast book comes first, subsumed the Wikidata duplicate, kept wikidataId, did NOT inflate show count
    first = merged.books[0]
    assert first.title == "The Temple of the Golden Pavilion"
    assert first.source == "podcast"
    assert first.wikidataId == "Q1145005"
    assert first.isbn == "9780679752707"
    assert first.uniqueShowCount == 1
    assert first.mentionCount == 1
    assert first.mentionedByShows == ["History of Japan Podcast"]

    second = merged.books[1]
    assert second.title == "A Cold Start Classic"
    assert second.source == "wikidata"

    # Search index formatting for wikidata vs podcast books
    search_items = build_search_index([merged])
    book_items = {item.title: item for item in search_items if item.kind == "Book"}
    assert book_items["A Cold Start Classic"].subtitle == "Set in Kyoto · Famous Author"
    assert book_items["A Cold Start Classic"].episodeId is None
    assert book_items["A Cold Start Classic"].timestampSec is None
    assert book_items["The Temple of the Golden Pavilion"].episodeId == "ep-1"
    assert book_items["The Temple of the Golden Pavilion"].timestampSec == 420


def test_wikidata_client_graceful_degradation_on_network_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def _failing_get(*args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(httpx.Client, "get", _failing_get)

    client = WikidataBookClient(cache_path=tmp_path / "wd_err.json")
    assert client.verify_book("Some Book", "Some Author") is None
    assert client.fetch_books_for_city("Kyoto", "Japan", geonames_id="1857910") == []
    # Transient errors must NOT be persisted as cached negative results
    assert not (tmp_path / "wd_err.json").exists()
