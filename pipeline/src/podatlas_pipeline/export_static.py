"""Static JSON/GeoJSON/RSS/KML/search-index/sitemap exporter for Cloudflare Pages."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .geocode import normalize_key
from .models import (
    BookRecord,
    Category,
    CitiesGeoJSON,
    CityData,
    CityFeatureProperties,
    CityGeoJSONFeature,
    GeoJSONPoint,
    PlaceOrGearRecord,
    SearchIndexItem,
)


def _format_mmss(seconds: int) -> str:
    mm, ss = divmod(max(0, int(seconds)), 60)
    return f"{mm:02d}:{ss:02d}"


def _format_rss_pubdate(iso_date: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_date.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    except Exception:
        return format_datetime(datetime(2026, 1, 1, tzinfo=timezone.utc))


def compute_primary_category(city: CityData) -> tuple[Category, list[Category]]:
    if not city.episodes:
        return city.primaryCategory, [city.primaryCategory]
    counts = Counter(ep.category for ep in city.episodes)
    ordered = [cat for cat, _ in counts.most_common()]
    return ordered[0], ordered


def apply_city_consensus(city: CityData) -> CityData:
    """Deduplicate books & places within a city, compute cross-show consensus, and sort by signal."""
    episode_shows = {ep.id: ep.show for ep in city.episodes}

    # --- Books consensus ---
    book_groups: dict[str, list[BookRecord]] = {}
    for book in city.books:
        key = (
            (book.openLibraryKey or "").strip().lower()
            or (book.isbn or "").replace("-", "").strip().lower()
            or f"{normalize_key(book.title)}|{normalize_key(book.author)}"
        )
        book_groups.setdefault(key, []).append(book)

    merged_books: list[BookRecord] = []
    for group in book_groups.values():
        lead = max(
            group,
            key=lambda b: (
                1 if b.endorsement == "strong_rec" else 0,
                1 if b.isbn else 0,
                len(b.quote),
            ),
        )
        shows: list[str] = []
        for item in group:
            for s in item.mentionedByShows or ([item.show] if item.show else []):
                if s and s not in shows:
                    shows.append(s)
        total_mentions = sum(max(1, item.mentionCount) for item in group)
        unique_shows = max(len(shows), max(item.uniqueShowCount for item in group), 1)
        endorsement = (
            "strong_rec"
            if any(item.endorsement == "strong_rec" for item in group)
            else "casual_mention"
        )
        isbn = lead.isbn or next((b.isbn for b in group if b.isbn), None)
        ol_key = lead.openLibraryKey or next((b.openLibraryKey for b in group if b.openLibraryKey), None)
        merged_books.append(
            lead.model_copy(
                update={
                    "isbn": isbn,
                    "openLibraryKey": ol_key,
                    "endorsement": endorsement,
                    "mentionCount": total_mentions,
                    "uniqueShowCount": unique_shows,
                    "mentionedByShows": shows,
                }
            )
        )

    merged_books.sort(
        key=lambda b: (
            -b.uniqueShowCount,
            0 if b.endorsement == "strong_rec" else 1,
            -b.mentionCount,
            b.title.lower(),
        )
    )

    # --- Places & Gear consensus ---
    place_groups: dict[str, list[PlaceOrGearRecord]] = {}
    for place in city.placesAndGear:
        key = f"{place.type}|{normalize_key(place.name)}"
        place_groups.setdefault(key, []).append(place)

    merged_places: list[PlaceOrGearRecord] = []
    for group in place_groups.values():
        lead = max(
            group,
            key=lambda p: (
                1 if p.endorsement == "strong_rec" else 0,
                1 if (p.lat is not None and p.lon is not None) else 0,
                len(p.note),
            ),
        )
        shows: list[str] = []
        for item in group:
            inferred_show = item.show or episode_shows.get(item.episodeId, "")
            for s in item.mentionedByShows or ([inferred_show] if inferred_show else []):
                if s and s not in shows:
                    shows.append(s)
        total_mentions = sum(max(1, item.mentionCount) for item in group)
        unique_shows = max(len(shows), max(item.uniqueShowCount for item in group), 1)
        endorsement = (
            "strong_rec"
            if any(item.endorsement == "strong_rec" for item in group)
            else "casual_mention"
        )
        lat = lead.lat if lead.lat is not None else next((p.lat for p in group if p.lat is not None), None)
        lon = lead.lon if lead.lon is not None else next((p.lon for p in group if p.lon is not None), None)
        lead_show = lead.show or episode_shows.get(lead.episodeId, "")
        merged_places.append(
            lead.model_copy(
                update={
                    "show": lead_show,
                    "lat": lat,
                    "lon": lon,
                    "endorsement": endorsement,
                    "mentionCount": total_mentions,
                    "uniqueShowCount": unique_shows,
                    "mentionedByShows": shows,
                }
            )
        )

    merged_places.sort(
        key=lambda p: (
            -p.uniqueShowCount,
            0 if p.endorsement == "strong_rec" else 1,
            -p.mentionCount,
            p.name.lower(),
        )
    )

    return city.model_copy(update={"books": merged_books, "placesAndGear": merged_places})


def build_cities_geojson(cities: list[CityData]) -> CitiesGeoJSON:
    features: list[CityGeoJSONFeature] = []
    for city in sorted(cities, key=lambda c: (-len(c.episodes), c.name)):
        primary_cat, all_cats = compute_primary_category(city)
        features.append(
            CityGeoJSONFeature(
                geometry=GeoJSONPoint(coordinates=(city.lon, city.lat)),
                properties=CityFeatureProperties(
                    id=city.id,
                    name=city.name,
                    country=city.country,
                    primaryCategory=primary_cat,
                    categories=all_cats,
                    episodeCount=len(city.episodes),
                    bookCount=len(city.books),
                    placeCount=len(city.placesAndGear),
                ),
            )
        )
    return CitiesGeoJSON(features=features)


def build_search_index(cities: list[CityData]) -> list[SearchIndexItem]:
    items: list[SearchIndexItem] = []
    for city in cities:
        items.append(
            SearchIndexItem(
                kind="City",
                title=f"{city.name}, {city.country}",
                subtitle=f"{len(city.episodes)} episodes · {len(city.books)} books · {len(city.placesAndGear)} places",
                cityId=city.id,
                tab="episodes",
            )
        )
        for ep in city.episodes:
            items.append(
                SearchIndexItem(
                    kind="Episode",
                    title=ep.title,
                    subtitle=f"{ep.show} · {city.name}",
                    cityId=city.id,
                    tab="episodes",
                    episodeId=ep.id,
                )
            )
        for book in city.books:
            consensus_prefix = (
                f"{book.uniqueShowCount} shows · " if book.uniqueShowCount >= 2 else ""
            )
            items.append(
                SearchIndexItem(
                    kind="Book",
                    title=book.title,
                    subtitle=f"{consensus_prefix}{book.author} · {city.name}",
                    cityId=city.id,
                    tab="books",
                    episodeId=book.episodeId,
                    timestampSec=book.timestampSec,
                )
            )
        for place in city.placesAndGear:
            items.append(
                SearchIndexItem(
                    kind=place.type,
                    title=place.name,
                    subtitle=f"{city.name} · {place.note[:64]}",
                    cityId=city.id,
                    tab="places",
                    episodeId=place.episodeId,
                    timestampSec=place.timestampSec,
                )
            )
    return items


def build_city_rss_xml(city: CityData, site_base_url: str) -> str:
    """Build a playable Podcast RSS 2.0 + iTunes playlist feed for a single city."""
    clean_base = site_base_url.rstrip("/")
    city_url = f"{clean_base}/city/{city.id}"
    feed_url = f"{clean_base}/feeds/{city.id}.xml"

    item_blocks: list[str] = []
    for ep in city.episodes:
        mention_lines = [
            f"• [{_format_mmss(m.sec)}] {m.label} ({m.type})" for m in ep.mentions
        ]
        desc_parts = [f"{ep.show}: {ep.summary}"]
        if mention_lines:
            desc_parts.append("Mentioned:\n" + "\n".join(mention_lines))
        description = "\n\n".join(desc_parts)

        enclosure_line = (
            f'      <enclosure url="{escape(ep.audioUrl)}" length="0" type="audio/mpeg" />'
            if ep.audioUrl
            else ""
        )
        lines = [
            "    <item>",
            f"      <title>{escape(ep.show)} — {escape(ep.title)}</title>",
            f"      <guid isPermaLink=\"false\">podatlas:{escape(city.id)}:{escape(ep.id)}</guid>",
            f"      <link>{escape(city_url)}</link>",
            f"      <pubDate>{escape(_format_rss_pubdate(ep.publishedAt))}</pubDate>",
            f"      <description>{escape(description)}</description>",
            f"      <itunes:author>{escape(ep.show)}</itunes:author>",
            f"      <itunes:duration>{int(ep.durationSec)}</itunes:duration>",
            f"      <itunes:summary>{escape(ep.summary)}</itunes:summary>",
        ]
        if enclosure_line:
            lines.append(enclosure_line)
        lines.append("    </item>")
        item_blocks.append("\n".join(lines))

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">',
            "  <channel>",
            f"    <title>PodAtlas: {escape(city.name)}, {escape(city.country)} Podcast Playlist</title>",
            f"    <link>{escape(city_url)}</link>",
            f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml" />',
            f"    <description>{escape(city.tagline)}</description>",
            "    <language>en-us</language>",
            "    <itunes:author>PodAtlas</itunes:author>",
            f"    <itunes:summary>{escape(city.tagline)}</itunes:summary>",
            *item_blocks,
            "  </channel>",
            "</rss>",
            "",
        ]
    )


def build_city_kml(city: CityData) -> str:
    """Build a Google My Maps / Organic Maps KML pin file for geocoded landmarks & food spots."""
    placemarks: list[str] = []
    for item in city.placesAndGear:
        if item.type == "Gear" or item.lat is None or item.lon is None:
            continue
        consensus_note = (
            f"Recommended by {item.uniqueShowCount} shows. "
            if item.uniqueShowCount >= 2
            else ""
        )
        show_note = f" ({item.show} @ {_format_mmss(item.timestampSec)})" if item.show else f" (@ {_format_mmss(item.timestampSec)})"
        desc = f"[{item.type}] {consensus_note}{item.note}{show_note}"
        placemarks.append(
            "\n".join(
                [
                    "    <Placemark>",
                    f"      <name>{escape(item.name)}</name>",
                    f"      <description>{escape(desc)}</description>",
                    "      <Point>",
                    f"        <coordinates>{item.lon},{item.lat},0</coordinates>",
                    "      </Point>",
                    "    </Placemark>",
                ]
            )
        )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<kml xmlns="http://www.opengis.net/kml/2.2">',
            "  <Document>",
            f"    <name>PodAtlas Pins — {escape(city.name)}, {escape(city.country)}</name>",
            f"    <description>{escape(city.tagline)}</description>",
            *placemarks,
            "  </Document>",
            "</kml>",
            "",
        ]
    )


def build_sitemap_xml(cities: list[CityData], base_url: str) -> str:
    clean_base = base_url.rstrip("/")
    urls = [f"  <url><loc>{escape(clean_base)}/</loc><priority>1.0</priority></url>"]
    for city in sorted(cities, key=lambda c: c.id):
        urls.append(
            f"  <url><loc>{escape(clean_base)}/city/{escape(city.id)}</loc><priority>0.8</priority></url>"
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            *urls,
            "</urlset>",
            "",
        ]
    )


def export_static_site_data(
    cities: list[CityData],
    output_dir: Path,
    site_base_url: str = "https://podatlas.app",
) -> dict[str, int]:
    """Write cities.geojson, data/{city}.json, feeds/{city}.xml, maps/{city}.kml, search-index.json, and sitemap.xml."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    feeds_dir = output_dir / "feeds"
    maps_dir = output_dir / "maps"
    data_dir.mkdir(parents=True, exist_ok=True)
    feeds_dir.mkdir(parents=True, exist_ok=True)
    maps_dir.mkdir(parents=True, exist_ok=True)

    normalized_cities = [apply_city_consensus(city) for city in cities]

    geojson = build_cities_geojson(normalized_cities)
    (output_dir / "cities.geojson").write_text(
        json.dumps(geojson.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    geocoded_places = 0
    for city in normalized_cities:
        (data_dir / f"{city.id}.json").write_text(
            json.dumps(city.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (feeds_dir / f"{city.id}.xml").write_text(
            build_city_rss_xml(city, site_base_url),
            encoding="utf-8",
        )
        (maps_dir / f"{city.id}.kml").write_text(
            build_city_kml(city),
            encoding="utf-8",
        )
        geocoded_places += sum(
            1 for p in city.placesAndGear if p.type != "Gear" and p.lat is not None and p.lon is not None
        )

    search_items = build_search_index(normalized_cities)
    (output_dir / "search-index.json").write_text(
        json.dumps([item.model_dump(mode="json", exclude_none=True) for item in search_items], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    sitemap = build_sitemap_xml(normalized_cities, site_base_url)
    (output_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    return {
        "cities": len(normalized_cities),
        "episodes": sum(len(c.episodes) for c in normalized_cities),
        "books": sum(len(c.books) for c in normalized_cities),
        "placesAndGear": sum(len(c.placesAndGear) for c in normalized_cities),
        "geocodedPlaces": geocoded_places,
        "rssFeeds": len(normalized_cities),
        "kmlMaps": len(normalized_cities),
        "searchEntries": len(search_items),
    }


def load_existing_cities(output_dir: Path) -> dict[str, CityData]:
    """Load existing /data/*.json files so incremental runs append cleanly."""
    data_dir = output_dir / "data"
    if not data_dir.exists():
        return {}
    loaded: dict[str, CityData] = {}
    for file_path in sorted(data_dir.glob("*.json")):
        city = CityData.model_validate_json(file_path.read_text(encoding="utf-8"))
        loaded[city.id] = city
    return loaded
