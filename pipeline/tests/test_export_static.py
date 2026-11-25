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
