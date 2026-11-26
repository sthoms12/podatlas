from pathlib import Path

from podatlas_pipeline.cli import (
    load_cached_transcripts,
    merge_extraction_into_cities,
    save_cached_transcript,
)
from podatlas_pipeline.geocode import GeoCity, GeoNamesIndex
from podatlas_pipeline.models import (
    CachedEpisodeTranscript,
    EpisodeExtraction,
    ExtractedBook,
    ExtractedCityRef,
    ExtractedPlaceOrGear,
    TranscriptSegment,
)
from podatlas_pipeline.podcast_index import RawEpisode, is_skippable_episode


def test_is_skippable_episode_filters_short_and_trailers():
    short_ep = RawEpisode(
        id="ep-short",
        guid="g-short",
        show_slug="test",
        show_title="Test Show",
        category="Travel",
        title="Quick Update",
        audio_url="https://example.com/a.mp3",
        duration_sec=180,
        published_at="2026-10-01",
        description="",
    )
    skip, _ = is_skippable_episode(short_ep, min_duration_sec=600)
    assert skip is True

    trailer_ep = RawEpisode(
        id="ep-trailer",
        guid="g-trailer",
        show_slug="test",
        show_title="Test Show",
        category="History",
        title="Season 4 Official Trailer",
        audio_url="https://example.com/b.mp3",
        duration_sec=0,
        published_at="2026-10-01",
        description="",
    )
    skip2, _ = is_skippable_episode(trailer_ep, min_duration_sec=600)
    assert skip2 is True

    real_ep = RawEpisode(
        id="ep-real",
        guid="g-real",
        show_slug="test",
        show_title="Test Show",
        category="History",
        title="Walking the Tokaido: Kyoto",
        audio_url="https://example.com/c.mp3",
        duration_sec=2880,
        published_at="2026-10-01",
        description="",
    )
    skip3, _ = is_skippable_episode(real_ep, min_duration_sec=600)
    assert skip3 is False


def test_cached_transcript_roundtrip(tmp_path: Path):
    item = CachedEpisodeTranscript(
        id="ky-1",
        guid="guid-1",
        showSlug="history-of-japan",
        showTitle="History of Japan Podcast",
        category="History",
        title="Kyoto in Edo",
        audioUrl="https://example.com/ky1.mp3",
        durationSec=2880,
        publishedAt="2026-10-01",
        transcriptSource="faster_whisper",
        segments=[TranscriptSegment(start=10.0, end=15.0, text="Welcome to Kyoto.")],
    )
    save_cached_transcript(tmp_path, item)
    loaded = load_cached_transcripts(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "ky-1"
    assert loaded[0].segments[0].text == "Welcome to Kyoto."


def test_merge_extraction_is_idempotent():
    geonames = GeoNamesIndex([GeoCity("kyoto-japan", "Kyoto", "Japan", "JP", 35.01, 135.77, 1465000)])
    cached_ep = CachedEpisodeTranscript(
        id="ky-1",
        guid="guid-1",
        showSlug="history-of-japan",
        showTitle="History of Japan Podcast",
        category="History",
        title="Kyoto in Edo",
        audioUrl="https://example.com/ky1.mp3",
        durationSec=2880,
        publishedAt="2026-10-01",
        transcriptSource="rss_tag",
        segments=[],
    )
    extraction = EpisodeExtraction(
        isDestinationFocused=True,
        primaryCities=[ExtractedCityRef(city="Kyoto", country="Japan", tagline="Zen temples.")],
        summary="Edo-era Kyoto.",
        books=[
            ExtractedBook(title="The Book of Tea", author="Kakuzo Okakura", timestampSec=620),
            ExtractedBook(title="The Book of Tea", author="Kakuzo Okakura", timestampSec=620),
        ],
        placesAndGear=[
            ExtractedPlaceOrGear(name="Honen-in Temple", type="Landmark", note="Quiet gate", timestampSec=1120),
            ExtractedPlaceOrGear(name="Honen-in Temple", type="Landmark", note="Duplicate", timestampSec=1120),
        ],
    )

    cities: dict = {}
    assert merge_extraction_into_cities(cities, cached_ep, extraction, geonames) == 1
    # Merging the same episode a second time must be a no-op
    assert merge_extraction_into_cities(cities, cached_ep, extraction, geonames) == 0

    kyoto = cities["kyoto-japan"]
    assert len(kyoto.episodes) == 1
    assert len(kyoto.books) == 1
    assert len(kyoto.placesAndGear) == 1
