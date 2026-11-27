"""Tests for <podcast:location> parsing, expanded COUNTRY_NAMES, shows.csv, and open dataset discovery."""

from pathlib import Path

from podatlas_pipeline.geocode import COUNTRY_NAMES
from podatlas_pipeline.open_datasets import (
    discover_from_locations,
    discover_from_podroll,
    export_discovered_csv,
    normalize_feed_url,
)
from podatlas_pipeline.podcast_index import ShowConfig, load_shows_csv, parse_podcast_locations


def test_parse_podcast_locations_prefers_subject_and_ignores_creator() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
      <channel>
        <title>Test Travel Show</title>
        <podcast:location rel="creator" country="US">Austin, TX</podcast:location>
        <podcast:location rel="subject" country="JP">Kyoto</podcast:location>
        <item>
          <guid>ep-1</guid>
          <title>Walking Kyoto</title>
          <podcast:location rel="creator" country="US">Austin Studio</podcast:location>
          <podcast:location rel="subject" country="JP">Gion, Kyoto</podcast:location>
        </item>
        <item>
          <guid>ep-2</guid>
          <title>Only Creator Tag</title>
          <podcast:location rel="creator" country="US">Austin Studio</podcast:location>
        </item>
      </channel>
    </rss>
    """
    channel_loc, item_locs = parse_podcast_locations(xml)
    assert channel_loc == "Kyoto, JP"
    assert item_locs["ep-1"] == "Gion, Kyoto, JP"
    assert "ep-2" not in item_locs


def test_country_names_covers_global_destinations() -> None:
    assert len(COUNTRY_NAMES) >= 240
    assert COUNTRY_NAMES["US"] == "USA"
    assert COUNTRY_NAMES["GB"] == "United Kingdom"
    assert COUNTRY_NAMES["KE"] == "Kenya"
    assert COUNTRY_NAMES["KH"] == "Cambodia"
    assert COUNTRY_NAMES["JO"] == "Jordan"
    assert COUNTRY_NAMES["NG"] == "Nigeria"
    assert COUNTRY_NAMES["EC"] == "Ecuador"


def test_shows_csv_has_100_plus_unique_valid_english_shows() -> None:
    shows_path = Path(__file__).resolve().parents[1] / "shows.csv"
    shows = load_shows_csv(shows_path)
    assert len(shows) >= 100
    slugs = [s.slug for s in shows]
    assert len(slugs) == len(set(slugs))
    normalized_urls = [normalize_feed_url(s.rss_url) for s in shows]
    assert len(normalized_urls) == len(set(normalized_urls))
    categories = {s.category for s in shows}
    assert categories == {"Travel", "History", "Food", "Culture"}
    assert all(s.rss_url.startswith(("http://", "https://")) for s in shows)


def test_discover_from_locations_filters_creator_and_dedupes() -> None:
    records = [
        {
            "feedId": 1,
            "medium": "podcast",
            "title": "Austin Tech Studio",
            "url": "https://example.com/creator.xml",
            "rel": "creator",
            "locale": "Austin, TX",
            "country": "US",
            "latlon": "30.26,-97.74",
        },
        {
            "feedId": 2,
            "medium": "podcast",
            "title": "Already Tracked Travel Show",
            "url": "https://example.com/tracked.xml/",
            "rel": "subject",
            "locale": "Tokyo",
            "country": "JP",
            "latlon": "35.68,139.76",
        },
        {
            "feedId": 3,
            "medium": "podcast",
            "title": "Hidden Kyoto Travel Guide",
            "url": "https://example.com/kyoto.xml",
            "rel": "subject",
            "locale": "Kyoto",
            "country": "JP",
            "latlon": "35.01,135.77",
        },
        {
            "feedId": 4,
            "medium": "podcast",
            "title": "Random Sports Banter",
            "url": "https://example.com/sports.xml",
            "rel": "subject",
            "locale": "Kyoto",
            "country": "JP",
            "latlon": "35.01,135.77",
        },
    ]
    existing = {normalize_feed_url("https://www.example.com/tracked.xml")}
    found = discover_from_locations(records, existing, country_filter="JP")
    assert len(found) == 1
    assert found[0].title == "Hidden Kyoto Travel Guide"
    assert found[0].suggestedCategory == "Travel"
    assert found[0].podcastIndexId == "3"


def test_discover_from_podroll_prioritizes_seed_recommendations_and_exports_csv(tmp_path: Path) -> None:
    existing_shows = [
        ShowConfig(
            slug="seed-show",
            title="Seed History Show",
            category="History",
            rss_url="https://example.com/seed.xml",
            podcast_index_id="999",
        )
    ]
    records = [
        {
            "popularity": 50,
            "feedId": 101,
            "medium": "podcast",
            "title": "World Food History",
            "url": "https://example.com/popular-food.xml",
            "sources": [{"feedId": 555, "url": "https://other.com/feed.xml"}],
        },
        {
            "popularity": 5,
            "feedId": 102,
            "medium": "podcast",
            "title": "Indie Silk Road Chronicles",
            "url": "https://example.com/silk-road.xml",
            "sources": [{"feedId": 999, "url": "https://example.com/seed.xml"}],
        },
    ]
    found = discover_from_podroll(records, existing_shows, limit=10)
    assert len(found) == 2
    # Direct podroll recommendation from a tracked show comes first
    assert found[0].title == "Indie Silk Road Chronicles"
    assert found[1].title == "World Food History"

    out_csv = export_discovered_csv(found, tmp_path / "candidates.csv")
    loaded = load_shows_csv(out_csv)
    assert len(loaded) == 2
    assert loaded[0].rss_url == "https://example.com/silk-road.xml"
