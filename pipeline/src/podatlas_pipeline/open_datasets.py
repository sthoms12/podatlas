"""Automated feed discovery using Podcast Index's free public open datasets.

Consumes:
  - https://public.podcastindex.org/locations.json (<podcast:location> tags)
  - https://public.podcastindex.org/recommendations.json (<podcast:podroll> graph)

Requires NO API key; only a descriptive application User-Agent header.
"""

from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from .models import Category
from .podcast_index import ShowConfig

LOCATIONS_DATASET_URL = "https://public.podcastindex.org/locations.json"
RECOMMENDATIONS_DATASET_URL = "https://public.podcastindex.org/recommendations.json"
USER_AGENT = "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"

# Keyword heuristics for inferring PodAtlas category and filtering high-signal shows
CATEGORY_KEYWORDS: dict[Category, tuple[str, ...]] = {
    "Travel": (
        "travel",
        "traveler",
        "traveller",
        "wanderlust",
        "nomad",
        "backpacker",
        "backpacking",
        "tourism",
        "road trip",
        "expedition",
        "destination",
        "destinations",
        "guide to",
        "abroad",
        "vacation",
        "packing",
        "onebag",
        "itinerary",
    ),
    "History": (
        "history",
        "historical",
        "ancient",
        "medieval",
        "empire",
        "empires",
        "dynasty",
        "archaeology",
        "archaeological",
        "heritage",
        "century",
        "chronicle",
        "chronicles",
        "civilization",
        "civilizations",
        "museum",
        "antiquity",
    ),
    "Food": (
        "food",
        "foodie",
        "culinary",
        "cuisine",
        "eating",
        "eats",
        "dining",
        "restaurant",
        "restaurants",
        "chef",
        "cooking",
        "gastronomy",
        "kitchen",
        "tasting",
        "wine",
        "coffee",
        "street food",
        "bakery",
        "bbq",
        "ramen",
    ),
    "Culture": (
        "culture",
        "cultural",
        "architecture",
        "urbanism",
        "urbanist",
        "cities",
        "folklore",
        "literature",
        "literary",
        "neighborhood",
        "neighborhoods",
        "art history",
        "museums",
    ),
}

_CATEGORY_PATTERNS: dict[Category, list[re.Pattern[str]]] = {
    cat: [re.compile(rf"\b{re.escape(kw)}\b", flags=re.IGNORECASE) for kw in kws]
    for cat, kws in CATEGORY_KEYWORDS.items()
}


@dataclass(frozen=True)
class DiscoveredFeed:
    slug: str
    title: str
    suggestedCategory: Category
    rssUrl: str
    podcastIndexId: str
    source: Literal["locations", "podroll"]
    locale: str = ""
    country: str = ""
    latlon: str = ""
    popularity: int = 0
    reason: str = ""


def normalize_feed_url(url: str) -> str:
    """Normalize an RSS URL for deduplication across http/https and trailing slashes."""
    raw = url.strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc.removeprefix("www.")
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def slugify_title(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:48] or "discovered-show"


def infer_category(text: str) -> Category | None:
    """Infer a PodAtlas category from title text using word-boundary matching."""
    best_cat: Category | None = None
    best_hits = 0
    for category, patterns in _CATEGORY_PATTERNS.items():
        hits = sum(1 for pat in patterns if pat.search(text))
        if hits > best_hits:
            best_hits = hits
            best_cat = category
    return best_cat


def fetch_or_load_dataset(
    url: str,
    cache_path: Path,
    max_age_hours: float = 24.0,
    timeout: float = 45.0,
) -> list[dict[str, Any]]:
    """Download a Podcast Index public dataset JSON file with local disk caching."""
    if cache_path.exists():
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600.0
        if age_hours < max_age_hours:
            return json.loads(cache_path.read_text(encoding="utf-8"))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        data = resp.json()

    cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def discover_from_locations(
    records: list[dict[str, Any]],
    existing_urls: set[str],
    *,
    country_filter: str = "",
    query: str = "",
    require_category_match: bool = True,
    limit: int = 25,
) -> list[DiscoveredFeed]:
    """Filter Podcast Index locations.json for subject-tagged Travel/History/Food/Culture feeds."""
    country_norm = country_filter.strip().upper()
    query_norm = query.strip().lower()
    seen_urls = set(existing_urls)
    candidates: list[DiscoveredFeed] = []

    for row in records:
        medium = (row.get("medium") or "podcast").strip().lower()
        if medium not in ("", "podcast"):
            continue
        rel = (row.get("rel") or "").strip().lower()
        if rel == "creator":
            continue

        rss_url = str(row.get("url") or "").strip()
        norm_url = normalize_feed_url(rss_url)
        if not norm_url or norm_url in seen_urls:
            continue

        title = str(row.get("title") or "").strip()
        locale = str(row.get("locale") or "").strip()
        country = str(row.get("country") or "").strip().upper()
        latlon = str(row.get("latlon") or "").strip()
        if not title or (not locale and not latlon):
            continue

        if country_norm and country != country_norm:
            continue
        if query_norm and query_norm not in f"{title} {locale}".lower():
            continue

        inferred = infer_category(title)
        if require_category_match and inferred is None:
            continue

        seen_urls.add(norm_url)
        candidates.append(
            DiscoveredFeed(
                slug=slugify_title(title),
                title=title,
                suggestedCategory=inferred or "Travel",
                rssUrl=rss_url,
                podcastIndexId=str(row.get("feedId") or ""),
                source="locations",
                locale=locale,
                country=country,
                latlon=latlon,
                reason=f"<podcast:location rel='{rel or 'unspecified'}'> ({locale or latlon})",
            )
        )

    # Prefer feeds with explicit rel='subject', coordinates, and country codes
    candidates.sort(
        key=lambda c: (
            "rel='subject'" in c.reason,
            bool(c.latlon),
            bool(c.country),
            c.title.lower(),
        ),
        reverse=True,
    )
    return candidates[:limit]


def discover_from_podroll(
    records: list[dict[str, Any]],
    existing_shows: list[ShowConfig],
    *,
    query: str = "",
    limit: int = 25,
) -> list[DiscoveredFeed]:
    """Walk Podcast Index recommendations.json (<podcast:podroll>) for peer-endorsed shows.

    Prioritizes shows directly recommended by feeds already in shows.csv, followed by
    high-popularity Travel/History/Food/Culture feeds in the podroll graph.
    """
    existing_urls = {normalize_feed_url(s.rss_url) for s in existing_shows}
    existing_ids = {s.podcast_index_id.strip() for s in existing_shows if s.podcast_index_id.strip()}
    query_norm = query.strip().lower()

    seen_urls = set(existing_urls)
    direct_recs: list[DiscoveredFeed] = []
    popular_niche: list[DiscoveredFeed] = []

    for row in records:
        medium = (row.get("medium") or "podcast").strip().lower()
        if medium not in ("", "podcast"):
            continue
        rss_url = str(row.get("url") or "").strip()
        norm_url = normalize_feed_url(rss_url)
        if not norm_url or norm_url in seen_urls:
            continue

        title = str(row.get("title") or "").strip()
        if not title:
            continue
        if query_norm and query_norm not in title.lower():
            continue

        popularity = int(row.get("popularity") or 0)
        sources = row.get("sources") or []
        seed_hit = False
        for src in sources:
            src_url = normalize_feed_url(str(src.get("url") or ""))
            src_id = str(src.get("feedId") or "").strip()
            if (src_url and src_url in existing_urls) or (src_id and src_id in existing_ids):
                seed_hit = True
                break

        inferred = infer_category(title)
        if not seed_hit and inferred is None:
            continue

        seen_urls.add(norm_url)
        candidate = DiscoveredFeed(
            slug=slugify_title(title),
            title=title,
            suggestedCategory=inferred or "Culture",
            rssUrl=rss_url,
            podcastIndexId=str(row.get("feedId") or ""),
            source="podroll",
            popularity=popularity,
            reason=(
                f"Recommended via <podcast:podroll> by a tracked show (popularity={popularity})"
                if seed_hit
                else f"Podroll niche match (popularity={popularity})"
            ),
        )
        if seed_hit:
            direct_recs.append(candidate)
        else:
            popular_niche.append(candidate)

    direct_recs.sort(key=lambda c: c.popularity, reverse=True)
    popular_niche.sort(key=lambda c: c.popularity, reverse=True)
    return (direct_recs + popular_niche)[:limit]


def export_discovered_csv(feeds: list[DiscoveredFeed], out_path: Path, default_max_episodes: int = 15) -> Path:
    """Write discovered feed candidates in shows.csv-compatible format for review."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["slug", "title", "category", "rss_url", "podcast_index_id", "max_episodes"])
        for f in feeds:
            writer.writerow(
                [f.slug, f.title, f.suggestedCategory, f.rssUrl, f.podcastIndexId, default_max_episodes]
            )
    return out_path


def discovered_feeds_to_dicts(feeds: list[DiscoveredFeed]) -> list[dict[str, Any]]:
    return [asdict(f) for f in feeds]
