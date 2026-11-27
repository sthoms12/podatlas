"""Wikidata P840 fallback book verifier and optional cold-start city book filler."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from .geocode import GeoNamesIndex, normalize_key
from .models import BookRecord, CityData
from .verify_books import VerifiedBook, _title_overlap, _tokens

WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"

_QID_RE = re.compile(r"^Q\d+$")
_QID_SEARCH_RE = re.compile(r"(Q\d+)$")
_OL_WORK_RE = re.compile(r"^OL\d+W$")
_OL_EDITION_RE = re.compile(r"^OL\d+M$")

# Common Wikidata instance-of (P31) classes for written/literary works
_BOOK_P31_QIDS: frozenset[str] = frozenset(
    {
        "Q7725634",  # literary work
        "Q571",      # book
        "Q47461344", # written work
        "Q8261",     # novel
        "Q277759",   # book series
        "Q49084",    # short story
        "Q3331189",  # version, edition or translation
        "Q20540385", # non-fiction work
        "Q386724",   # work
        "Q1667921",  # novel series
        "Q213051",   # non-fiction
        "Q109838",   # memoir
        "Q36279",    # biography
        "Q4184",     # autobiography
        "Q35760",    # essay
        "Q1238720",  # political treatise
        "Q149537",   # novella
        "Q223770",   # travel literature
    }
)


def _is_raw_qid(text: str) -> bool:
    return bool(_QID_RE.fullmatch(text.strip()))


def _extract_qid(uri_or_id: str) -> str:
    m = _QID_SEARCH_RE.search(uri_or_id.strip())
    return m.group(1) if m else ""


def normalize_open_library_id(raw: str | None) -> str | None:
    """Normalize Wikidata P648 Open Library IDs into canonical /works/OL...W or /books/OL...M keys."""
    if not raw:
        return None
    val = str(raw).strip()
    if not val:
        return None
    if val.startswith(("/works/", "/books/", "/authors/")):
        return val
    stripped = val.lstrip("/")
    if _OL_WORK_RE.fullmatch(stripped):
        return f"/works/{stripped}"
    if _OL_EDITION_RE.fullmatch(stripped):
        return f"/books/{stripped}"
    if stripped.startswith("OL"):
        return f"/works/{stripped}"
    return None


def _clean_isbn(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", str(raw)).upper()
    if len(cleaned) in (10, 13):
        return cleaned
    return None


def _extract_claim_strings(claims: dict[str, Any], prop: str) -> list[str]:
    values: list[str] = []
    for stmt in claims.get(prop) or []:
        if not isinstance(stmt, dict):
            continue
        mainsnak = stmt.get("mainsnak") or {}
        datavalue = mainsnak.get("datavalue") or {}
        val = datavalue.get("value")
        if isinstance(val, str) and val.strip():
            values.append(val.strip())
    return values


def _extract_claim_entity_ids(claims: dict[str, Any], prop: str) -> list[str]:
    ids: list[str] = []
    for stmt in claims.get(prop) or []:
        if not isinstance(stmt, dict):
            continue
        mainsnak = stmt.get("mainsnak") or {}
        datavalue = mainsnak.get("datavalue") or {}
        val = datavalue.get("value")
        if isinstance(val, dict) and isinstance(val.get("id"), str):
            ids.append(val["id"].strip())
    return ids


def _escape_sparql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').strip()


class WikidataBookClient:
    """Disk-cached Wikidata client for fallback book verification and P840 city book queries."""

    def __init__(self, cache_path: Path | None = None, timeout: float = 20.0) -> None:
        self.cache_path = cache_path
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any]] = {"verify": {}, "city_books": {}}
        if cache_path and cache_path.exists():
            try:
                loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    if isinstance(loaded.get("verify"), dict):
                        self._cache["verify"] = loaded["verify"]
                    if isinstance(loaded.get("city_books"), dict):
                        self._cache["city_books"] = loaded["city_books"]
            except Exception:
                self._cache = {"verify": {}, "city_books": {}}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _api_get(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
        resp = client.get(
            WIKIDATA_API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def verify_book(self, title: str, author: str = "") -> VerifiedBook | None:
        """Role A: Verify a book title/author against Wikidata entities (fallback when Open Library misses)."""
        clean_title = title.strip()
        clean_author = author.strip()
        if not clean_title:
            return None

        cache_key = f"{clean_title.lower()}::{clean_author.lower()}"
        if cache_key in self._cache["verify"]:
            cached = self._cache["verify"][cache_key]
            return VerifiedBook(**cached) if cached else None

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                search_data = self._api_get(
                    client,
                    {
                        "action": "wbsearchentities",
                        "search": clean_title,
                        "language": "en",
                        "type": "item",
                        "limit": 6,
                        "format": "json",
                    },
                )
                search_hits = search_data.get("search") or []
                qids = [
                    str(hit["id"]).strip()
                    for hit in search_hits
                    if isinstance(hit, dict) and hit.get("id")
                ]
                if not qids:
                    self._cache["verify"][cache_key] = None
                    self._save_cache()
                    return None

                entities_data = self._api_get(
                    client,
                    {
                        "action": "wbgetentities",
                        "ids": "|".join(qids),
                        "props": "labels|aliases|claims",
                        "languages": "en",
                        "format": "json",
                    },
                )
                entities = entities_data.get("entities") or {}

                if not entities and search_hits:
                    author_tokens_fb = _tokens(clean_author)
                    book_desc_terms = {"book", "novel", "memoir", "biography", "history", "non-fiction", "nonfiction", "work", "essay", "story", "poem", "monograph"}
                    for hit in search_hits:
                        if not isinstance(hit, dict):
                            continue
                        hid = str(hit.get("id") or "").strip()
                        hlabel = str(hit.get("label") or "").strip()
                        hdesc = str(hit.get("description") or "").strip()
                        if not hid or not hlabel or _is_raw_qid(hlabel):
                            continue
                        if _title_overlap(clean_title, hlabel) < 0.5:
                            continue
                        desc_tokens = _tokens(hdesc)
                        has_book_signal = bool(desc_tokens & book_desc_terms)
                        has_author_signal = bool(author_tokens_fb and (desc_tokens & author_tokens_fb))
                        if not (has_book_signal or has_author_signal):
                            continue
                        verified = VerifiedBook(
                            title=hlabel,
                            author=clean_author or "Unknown Author",
                            isbn=None,
                            open_library_key=None,
                            wikidata_id=hid,
                        )
                        self._cache["verify"][cache_key] = {
                            "title": verified.title,
                            "author": verified.author,
                            "isbn": verified.isbn,
                            "open_library_key": verified.open_library_key,
                            "wikidata_id": verified.wikidata_id,
                        }
                        self._save_cache()
                        return verified

                # Collect author Q-IDs (P50) across candidates and batch-resolve labels
                author_qids: list[str] = []
                for qid in qids:
                    entity = entities.get(qid) or {}
                    claims = entity.get("claims") or {}
                    for aqid in _extract_claim_entity_ids(claims, "P50"):
                        if aqid not in author_qids:
                            author_qids.append(aqid)

                author_labels: dict[str, str] = {}
                if author_qids:
                    authors_data = self._api_get(
                        client,
                        {
                            "action": "wbgetentities",
                            "ids": "|".join(author_qids[:20]),
                            "props": "labels",
                            "languages": "en",
                            "format": "json",
                        },
                    )
                    for aqid, aent in (authors_data.get("entities") or {}).items():
                        lbl = str(((aent.get("labels") or {}).get("en") or {}).get("value") or "").strip()
                        if lbl and not _is_raw_qid(lbl):
                            author_labels[aqid] = lbl
        except Exception:
            return None

        author_tokens = _tokens(clean_author)
        best_score = 0.0
        best_book: VerifiedBook | None = None

        for qid in qids:
            entity = entities.get(qid)
            if not isinstance(entity, dict):
                continue
            labels = entity.get("labels") or {}
            cand_label = str((labels.get("en") or {}).get("value") or "").strip()
            if not cand_label or _is_raw_qid(cand_label):
                continue

            aliases_en = [
                str(a.get("value") or "").strip()
                for a in ((entity.get("aliases") or {}).get("en") or [])
                if isinstance(a, dict) and str(a.get("value") or "").strip()
            ]
            name_candidates = [cand_label, *aliases_en]
            title_score = max(_title_overlap(clean_title, nm) for nm in name_candidates)
            if title_score < 0.5:
                continue

            claims = entity.get("claims") or {}
            p31_ids = set(_extract_claim_entity_ids(claims, "P31"))
            p50_ids = _extract_claim_entity_ids(claims, "P50")
            p2093_names = _extract_claim_strings(claims, "P2093")
            isbn13s = [c for c in (_clean_isbn(x) for x in _extract_claim_strings(claims, "P212")) if c]
            isbn10s = [c for c in (_clean_isbn(x) for x in _extract_claim_strings(claims, "P957")) if c]
            ol_raws = _extract_claim_strings(claims, "P648")

            has_book_signal = bool(
                (p31_ids & _BOOK_P31_QIDS) or p50_ids or p2093_names or isbn13s or isbn10s or ol_raws
            )
            if not has_book_signal:
                continue

            resolved_authors = [author_labels[aqid] for aqid in p50_ids if aqid in author_labels]
            if not resolved_authors and p2093_names:
                resolved_authors = [n for n in p2093_names if not _is_raw_qid(n)]

            cand_author = resolved_authors[0] if resolved_authors else (clean_author or "Unknown Author")
            author_bonus = 0.0
            if author_tokens and resolved_authors:
                cand_author_tokens = _tokens(" ".join(resolved_authors))
                if author_tokens & cand_author_tokens:
                    author_bonus = 0.35
                elif title_score < 0.8:
                    # Explicit author mismatch on a partial title match -> likely a different book
                    continue

            isbn = isbn13s[0] if isbn13s else (isbn10s[0] if isbn10s else None)
            ol_keys = [k for k in (normalize_open_library_id(x) for x in ol_raws) if k]
            ol_work = next((k for k in ol_keys if k.startswith("/works/")), ol_keys[0] if ol_keys else None)

            isbn_bonus = 0.05 if isbn else 0.0
            ol_bonus = 0.05 if ol_work else 0.0
            score = title_score + author_bonus + isbn_bonus + ol_bonus
            if score > best_score:
                best_score = score
                best_book = VerifiedBook(
                    title=cand_label,
                    author=cand_author,
                    isbn=isbn,
                    open_library_key=ol_work,
                    wikidata_id=qid,
                )

        self._cache["verify"][cache_key] = (
            {
                "title": best_book.title,
                "author": best_book.author,
                "isbn": best_book.isbn,
                "open_library_key": best_book.open_library_key,
                "wikidata_id": best_book.wikidata_id,
            }
            if best_book
            else None
        )
        self._save_cache()
        return best_book

    def fetch_books_for_city(
        self,
        city_name: str,
        country: str = "",
        geonames_id: str = "",
        limit: int = 5,
    ) -> list[VerifiedBook]:
        """Role B: Query Wikidata P840 (narrative location) for notable books set in a city."""
        clean_gn = geonames_id.strip()
        if not clean_gn and not country.strip() and city_name.strip().isdigit():
            clean_gn = city_name.strip()
        cache_key = (
            f"gn:{clean_gn}:{limit}"
            if clean_gn
            else f"name:{normalize_key(city_name)}|{normalize_key(country)}:{limit}"
        )
        if cache_key in self._cache["city_books"]:
            cached_list = self._cache["city_books"][cache_key]
            return [VerifiedBook(**item) for item in cached_list]

        if clean_gn:
            city_clause = f'?city wdt:P1566 "{_escape_sparql_literal(clean_gn)}" .'
        else:
            esc_city = _escape_sparql_literal(city_name)
            if not esc_city:
                return []
            if country.strip():
                esc_country = _escape_sparql_literal(country)
                city_clause = (
                    f'?city rdfs:label "{esc_city}"@en .\n'
                    f"  ?city wdt:P17 ?countryEntity .\n"
                    f'  ?countryEntity rdfs:label "{esc_country}"@en .'
                )
            else:
                city_clause = f'?city rdfs:label "{esc_city}"@en .'

        sparql = f"""
SELECT ?book ?bookLabel ?authorLabel ?isbn13 ?isbn10 ?olId ?sitelinks WHERE {{
  {city_clause}
  ?book wdt:P840 ?city .
  ?book wdt:P31/wdt:P279* wd:Q7725634 .
  OPTIONAL {{ ?book wdt:P50 ?author . }}
  OPTIONAL {{ ?book wdt:P212 ?isbn13 . }}
  OPTIONAL {{ ?book wdt:P957 ?isbn10 . }}
  OPTIONAL {{ ?book wdt:P648 ?olId . }}
  OPTIONAL {{ ?book wikibase:sitelinks ?sitelinks . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {max(limit * 4, 20)}
""".strip()

        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(
                    WIKIDATA_SPARQL_URL,
                    params={"query": sparql, "format": "json"},
                    headers={
                        "Accept": "application/sparql-results+json",
                        "User-Agent": USER_AGENT,
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
            bindings = (payload.get("results") or {}).get("bindings") or []
        except Exception:
            return []

        # Deduplicate rows by book Q-ID, preserving sitelinks DESC order
        by_qid: dict[str, dict[str, Any]] = {}
        ordered_qids: list[str] = []

        for row in bindings:
            if not isinstance(row, dict):
                continue
            book_uri = str((row.get("book") or {}).get("value") or "").strip()
            qid = _extract_qid(book_uri)
            if not qid:
                continue

            title_val = str((row.get("bookLabel") or {}).get("value") or "").strip()
            if not title_val or _is_raw_qid(title_val):
                continue

            if qid not in by_qid:
                by_qid[qid] = {
                    "title": title_val,
                    "author": None,
                    "isbn13": None,
                    "isbn10": None,
                    "ol_work": None,
                    "ol_other": None,
                }
                ordered_qids.append(qid)

            entry = by_qid[qid]
            author_val = str((row.get("authorLabel") or {}).get("value") or "").strip()
            if author_val and not _is_raw_qid(author_val) and not entry["author"]:
                entry["author"] = author_val

            isbn13_val = _clean_isbn((row.get("isbn13") or {}).get("value"))
            if isbn13_val and not entry["isbn13"]:
                entry["isbn13"] = isbn13_val

            isbn10_val = _clean_isbn((row.get("isbn10") or {}).get("value"))
            if isbn10_val and not entry["isbn10"]:
                entry["isbn10"] = isbn10_val

            ol_norm = normalize_open_library_id((row.get("olId") or {}).get("value"))
            if ol_norm:
                if ol_norm.startswith("/works/") and not entry["ol_work"]:
                    entry["ol_work"] = ol_norm
                elif not entry["ol_other"]:
                    entry["ol_other"] = ol_norm

        books: list[VerifiedBook] = []
        for qid in ordered_qids[:limit]:
            entry = by_qid[qid]
            books.append(
                VerifiedBook(
                    title=entry["title"],
                    author=entry["author"] or "Unknown Author",
                    isbn=entry["isbn13"] or entry["isbn10"],
                    open_library_key=entry["ol_work"] or entry["ol_other"],
                    wikidata_id=qid,
                )
            )

        self._cache["city_books"][cache_key] = [
            {
                "title": b.title,
                "author": b.author,
                "isbn": b.isbn,
                "open_library_key": b.open_library_key,
                "wikidata_id": b.wikidata_id,
            }
            for b in books
        ]
        self._save_cache()
        return books


def fill_cold_start_books(
    cities_by_id: dict[str, CityData],
    wikidata_client: WikidataBookClient,
    geonames_index: GeoNamesIndex | None = None,
    min_books: int = 3,
    max_add: int = 5,
) -> int:
    """Optionally fill cold-start cities (< min_books) with Wikidata P840 books."""
    total_added = 0
    for city in cities_by_id.values():
        if len(city.books) >= min_books:
            continue

        geonames_id = ""
        if geonames_index is not None:
            geo = geonames_index.lookup(city.name, city.country)
            if geo is not None:
                geonames_id = geo.geonames_id

        candidates = wikidata_client.fetch_books_for_city(
            city.name,
            city.country,
            geonames_id=geonames_id,
            limit=max_add,
        )

        existing_wids = {
            b.wikidataId.strip().upper()
            for b in city.books
            if b.wikidataId and b.wikidataId.strip()
        }
        existing_ol = {
            b.openLibraryKey.strip().lower()
            for b in city.books
            if b.openLibraryKey and b.openLibraryKey.strip()
        }
        existing_isbns = {
            b.isbn.replace("-", "").strip().lower()
            for b in city.books
            if b.isbn and b.isbn.strip()
        }
        existing_ta = {
            f"{normalize_key(b.title)}|{normalize_key(b.author)}"
            for b in city.books
        }

        added_for_city = 0
        for cand in candidates:
            if added_for_city >= max_add:
                break

            wid_key = cand.wikidata_id.strip().upper() if cand.wikidata_id else ""
            ol_key = cand.open_library_key.strip().lower() if cand.open_library_key else ""
            isbn_key = cand.isbn.replace("-", "").strip().lower() if cand.isbn else ""
            ta_key = f"{normalize_key(cand.title)}|{normalize_key(cand.author)}"

            if (
                (wid_key and wid_key in existing_wids)
                or (ol_key and ol_key in existing_ol)
                or (isbn_key and isbn_key in existing_isbns)
                or (ta_key in existing_ta)
            ):
                continue

            city.books.append(
                BookRecord(
                    title=cand.title,
                    author=cand.author,
                    isbn=cand.isbn,
                    openLibraryKey=cand.open_library_key,
                    wikidataId=cand.wikidata_id,
                    source="wikidata",
                    episodeId="",
                    show="",
                    timestampSec=0,
                    quote=f"Set in {city.name} (Wikidata narrative location).",
                    endorsement="casual_mention",
                    mentionCount=1,
                    uniqueShowCount=1,
                    mentionedByShows=[],
                )
            )
            if wid_key:
                existing_wids.add(wid_key)
            if ol_key:
                existing_ol.add(ol_key)
            if isbn_key:
                existing_isbns.add(isbn_key)
            existing_ta.add(ta_key)
            added_for_city += 1
            total_added += 1

    return total_added
