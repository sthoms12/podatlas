import json
from pathlib import Path

from podatlas_pipeline.export_static import export_static_site_data, load_existing_cities
from podatlas_pipeline.models import BookRecord, CityData, EpisodeMention, EpisodeRecord, PlaceOrGearRecord


def test_export_and_reload_static_site_data(tmp_path: Path):
    city = CityData(
        id="kyoto-japan",
        name="Kyoto",
        country="Japan",
        lat=35.01,
        lon=135.77,
        primaryCategory="History",
        tagline="Former imperial capital.",
        episodes=[
            EpisodeRecord(
                id="ky-1",
                show="History of Japan Podcast",
                title="Walking the Tokaido",
                category="History",
                audioUrl="https://example.com/ep1.mp3",
                durationSec=2880,
                publishedAt="2026-09-14",
                summary="Traces merchant life in Kyoto.",
                mentions=[EpisodeMention(sec=840, type="book", label="Kyoto: A Cultural History")],
            )
        ],
        books=[
            BookRecord(
                title="Kyoto: A Cultural History",
                author="John Dougill",
                isbn="9780195301380",
                episodeId="ky-1",
                show="History of Japan Podcast",
                timestampSec=840,
                quote="Best primer.",
            )
        ],
        placesAndGear=[
            PlaceOrGearRecord(
                name="Honen-in Temple",
                type="Landmark",
                note="Moss-covered gate.",
                episodeId="ky-1",
                timestampSec=1120,
            )
        ],
    )

    stats = export_static_site_data([city], output_dir=tmp_path, site_base_url="https://podatlas.example")
    assert stats["cities"] == 1
    assert stats["episodes"] == 1
    assert stats["books"] == 1
    assert stats["placesAndGear"] == 1

    geojson = json.loads((tmp_path / "cities.geojson").read_text(encoding="utf-8"))
    assert geojson["features"][0]["geometry"]["coordinates"] == [135.77, 35.01]
    assert geojson["features"][0]["properties"]["id"] == "kyoto-japan"

    search_index = json.loads((tmp_path / "search-index.json").read_text(encoding="utf-8"))
    assert len(search_index) == 4  # City + Episode + Book + Landmark

    sitemap = (tmp_path / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://podatlas.example/city/kyoto-japan" in sitemap

    reloaded = load_existing_cities(tmp_path)
    assert "kyoto-japan" in reloaded
    assert reloaded["kyoto-japan"].books[0].isbn == "9780195301380"

    rss_xml = (tmp_path / "feeds" / "kyoto-japan.xml").read_text(encoding="utf-8")
    assert '<rss version="2.0"' in rss_xml
    assert 'enclosure url="https://example.com/ep1.mp3"' in rss_xml
    assert "History of Japan Podcast — Walking the Tokaido" in rss_xml

    kml_xml = (tmp_path / "maps" / "kyoto-japan.kml").read_text(encoding="utf-8")
    assert '<kml xmlns="http://www.opengis.net/kml/2.2">' in kml_xml


def test_consensus_aggregation_and_kml_coordinates(tmp_path: Path):
    from podatlas_pipeline.export_static import apply_city_consensus, build_city_kml

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
                show="Show A",
                title="Ep 1",
                category="History",
                durationSec=1800,
                publishedAt="2026-09-01",
                summary="Summary 1",
            ),
            EpisodeRecord(
                id="ep-2",
                show="Show B",
                title="Ep 2",
                category="Travel",
                durationSec=2000,
                publishedAt="2026-09-02",
                summary="Summary 2",
            ),
        ],
        books=[
            BookRecord(
                title="Casual Mention Book",
                author="Someone",
                episodeId="ep-1",
                show="Show A",
                timestampSec=100,
                endorsement="casual_mention",
            ),
            BookRecord(
                title="Kyoto: A Cultural History",
                author="John Dougill",
                isbn="9780195301380",
                episodeId="ep-1",
                show="Show A",
                timestampSec=200,
                quote="Great primer.",
                endorsement="casual_mention",
            ),
            BookRecord(
                title="Kyoto: A Cultural History",
                author="John Dougill",
                isbn="9780195301380",
                episodeId="ep-2",
                show="Show B",
                timestampSec=300,
                quote="Must read.",
                endorsement="strong_rec",
            ),
        ],
        placesAndGear=[
            PlaceOrGearRecord(
                name="Honen-in Temple",
                type="Landmark",
                note="Quiet temple.",
                episodeId="ep-1",
                timestampSec=400,
                show="Show A",
                lat=35.0221,
                lon=135.7973,
            ),
            PlaceOrGearRecord(
                name="Honen-in Temple",
                type="Landmark",
                note="Mossy gate.",
                episodeId="ep-2",
                timestampSec=500,
                show="Show B",
            ),
            PlaceOrGearRecord(
                name="Travel Backpack",
                type="Gear",
                note="Fits lockers.",
                episodeId="ep-2",
                timestampSec=600,
                show="Show B",
            ),
        ],
    )

    merged = apply_city_consensus(city)
    assert len(merged.books) == 2
    assert merged.books[0].title == "Kyoto: A Cultural History"
    assert merged.books[0].uniqueShowCount == 2
    assert merged.books[0].mentionCount == 2
    assert merged.books[0].endorsement == "strong_rec"
    assert set(merged.books[0].mentionedByShows) == {"Show A", "Show B"}

    assert len(merged.placesAndGear) == 2
    assert merged.placesAndGear[0].name == "Honen-in Temple"
    assert merged.placesAndGear[0].uniqueShowCount == 2
    assert merged.placesAndGear[0].lat == 35.0221
    assert merged.placesAndGear[0].lon == 135.7973

    kml = build_city_kml(merged)
    assert "<coordinates>135.7973,35.0221,0</coordinates>" in kml
    assert "Recommended by 2 shows." in kml
    assert "Travel Backpack" not in kml
