"""Static JSON/GeoJSON/search-index/sitemap exporter for Cloudflare Pages."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from .models import (
    Category,
    CitiesGeoJSON,
    CityData,
    CityFeatureProperties,
    CityGeoJSONFeature,
    GeoJSONPoint,
    SearchIndexItem,
)


def compute_primary_category(city: CityData) -> tuple[Category, list[Category]]:
    if not city.episodes:
        return city.primaryCategory, [city.primaryCategory]
    counts = Counter(ep.category for ep in city.episodes)
    ordered = [cat for cat, _ in counts.most_common()]
    return ordered[0], ordered


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
            items.append(
                SearchIndexItem(
                    kind="Book",
                    title=book.title,
                    subtitle=f"{book.author} · {city.name}",
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
    site_base_url: str = "https://podatlas.pages.dev",
) -> dict[str, int]:
    """Write cities.geojson, data/{city-slug}.json, search-index.json, and sitemap.xml."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    geojson = build_cities_geojson(cities)
    (output_dir / "cities.geojson").write_text(
        json.dumps(geojson.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    for city in cities:
        (data_dir / f"{city.id}.json").write_text(
            json.dumps(city.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    search_items = build_search_index(cities)
    (output_dir / "search-index.json").write_text(
        json.dumps([item.model_dump(mode="json", exclude_none=True) for item in search_items], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    sitemap = build_sitemap_xml(cities, site_base_url)
    (output_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")

    return {
        "cities": len(cities),
        "episodes": sum(len(c.episodes) for c in cities),
        "books": sum(len(c.books) for c in cities),
        "placesAndGear": sum(len(c.placesAndGear) for c in cities),
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
