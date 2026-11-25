"""Book verification against the free Open Library Search API."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"


@dataclass(frozen=True)
class VerifiedBook:
    title: str
    author: str
    isbn: str | None
    open_library_key: str | None


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}


def _title_overlap(query_title: str, candidate_title: str) -> float:
    q = _tokens(query_title)
    c = _tokens(candidate_title)
    if not q or not c:
        return 0.0
    return len(q & c) / len(q)


def pick_best_open_library_doc(
    query_title: str,
    query_author: str,
    docs: list[dict[str, Any]],
    min_title_overlap: float = 0.5,
) -> VerifiedBook | None:
    """Select the highest-confidence Open Library match or return None if likely hallucinated."""
    best_score = 0.0
    best_doc: dict[str, Any] | None = None

    author_tokens = _tokens(query_author)
    for doc in docs[:8]:
        cand_title = str(doc.get("title") or "").strip()
        if not cand_title:
            continue
        title_score = _title_overlap(query_title, cand_title)
        if title_score < min_title_overlap:
            continue

        cand_authors = doc.get("author_name") or []
        author_bonus = 0.0
        if author_tokens and cand_authors:
            cand_author_tokens = _tokens(" ".join(cand_authors))
            if author_tokens & cand_author_tokens:
                author_bonus = 0.35

        isbn_bonus = 0.05 if doc.get("isbn") else 0.0
        score = title_score + author_bonus + isbn_bonus
        if score > best_score:
            best_score = score
            best_doc = doc

    if not best_doc:
        return None

    isbns = [str(i) for i in (best_doc.get("isbn") or []) if len(str(i)) in (10, 13)]
    isbn13 = next((i for i in isbns if len(i) == 13), isbns[0] if isbns else None)
    authors = best_doc.get("author_name") or []
    author = authors[0] if authors else (query_author or "Unknown Author")
    return VerifiedBook(
        title=str(best_doc["title"]).strip(),
        author=str(author).strip(),
        isbn=isbn13,
        open_library_key=best_doc.get("key"),
    )


class OpenLibraryVerifier:
    """Caches Open Library book lookups on disk to stay polite and fast."""

    def __init__(self, cache_path: Path | None = None, timeout: float = 15.0) -> None:
        self.cache_path = cache_path
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any] | None] = {}
        if cache_path and cache_path.exists():
            self._cache = json.loads(cache_path.read_text(encoding="utf-8"))

    def _save_cache(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def verify(self, title: str, author: str = "") -> VerifiedBook | None:
        cache_key = f"{title.strip().lower()}::{author.strip().lower()}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return VerifiedBook(**cached) if cached else None

        params: dict[str, Any] = {"title": title, "limit": 6}
        if author:
            params["author"] = author

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                OPEN_LIBRARY_SEARCH_URL,
                params=params,
                headers={"User-Agent": "PodAtlas/0.1 (+https://github.com/sthoms12/podatlas)"},
            )
            resp.raise_for_status()
            docs = resp.json().get("docs", [])

        verified = pick_best_open_library_doc(title, author, docs)
        self._cache[cache_key] = (
            {
                "title": verified.title,
                "author": verified.author,
                "isbn": verified.isbn,
                "open_library_key": verified.open_library_key,
            }
            if verified
            else None
        )
        self._save_cache()
        return verified
