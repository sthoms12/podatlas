"""Offline unit tests for Milestone M1: Substack <content:encoded> fallback & F4 schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pydantic
import pytest

from podatlas_pipeline import cli, podcast_index
from podatlas_pipeline.config import PipelineSettings
from podatlas_pipeline.models import (
    BookRecord,
    CachedEpisodeTranscript,
    CityData,
    EpisodeRecord,
    PlaceOrGearRecord,
    TranscriptSegment,
)
from podatlas_pipeline.podcast_index import (
    RawEpisode,
    ShowConfig,
    check_show_feed,
    fetch_rss_episodes,
    is_skippable_episode,
    parse_content_encoded_to_segments,
)


LONG_ESSAY_HTML = """
<style>body { color: red; } .banner { display: none; }</style>
<script>console.log("should never appear in transcript");</script>
<h2>Walking Through Lisbon&#8217;s Alfama District</h2>
<p>Alfama is Lisbon&rsquo;s oldest neighborhood, a labyrinth of steep cobblestone alleys,
fado houses, and tiled facades that survived the 1755 earthquake. Start early at Miradouro
de Santa Luzia before the tram queues build up along the ridge.</p>
<p>For lunch, stop by Taberna da Rua das Flores &amp; order whatever is chalked on the
daily slate&nbsp;board. Pair it with a cold Vinho Verde and a copy of Fernando Pessoa&#8217;s
<em>The Book of Disquiet</em>, which captures the melancholy beauty of Baixa and Chiado.</p>
<blockquote>Every corner in Lisbon opens onto another tiled staircase and a glimpse of the Tagus River.</blockquote>
"""

SHORT_SHOW_NOTES_HTML = "<p>In this episode we talk about Lisbon. Support us on Patreon!</p>"


def _make_rss_xml(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0">'
        "<channel>"
        "<title>Lisbon Letters</title>"
        "<link>https://example.substack.com</link>"
        "<description>Travel essays with audio.</description>"
        f"{items_xml}"
        "</channel></rss>"
    )


def _mock_httpx_get(monkeypatch: pytest.MonkeyPatch, xml_text: str) -> None:
    fake_resp = MagicMock()
    fake_resp.text = xml_text
    fake_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_resp

    monkeypatch.setattr(httpx, "Client", lambda *args, **kwargs: fake_client)


def test_fetch_rss_episodes_captures_content_encoded_and_enclosures(monkeypatch: pytest.MonkeyPatch):
    items_xml = f"""
    <item>
      <title>Alfama Essay</title>
      <guid>sub-1</guid>
      <itunes:duration>12:30</itunes:duration>
      <enclosure url="https://cdn.substack.com/audio/alfama.mp3?token=abc123&amp;exp=999" length="12345" type="application/octet-stream" />
      <content:encoded><![CDATA[{LONG_ESSAY_HTML}]]></content:encoded>
    </item>
    <item>
      <title>Chiado Audio Note</title>
      <guid>sub-2</guid>
      <itunes:duration>900</itunes:duration>
      <enclosure url="https://cdn.substack.com/audio/chiado.M4A?sig=xyz" length="54321" type="" />
      <content:encoded><![CDATA[]]></content:encoded>
    </item>
    <item>
      <title>Image Only Post</title>
      <guid>sub-3</guid>
      <enclosure url="https://cdn.substack.com/images/cover.jpg" length="999" type="image/jpeg" />
      <content:encoded><![CDATA[{LONG_ESSAY_HTML}]]></content:encoded>
    </item>
    """
    _mock_httpx_get(monkeypatch, _make_rss_xml(items_xml))

    show = ShowConfig(
        slug="lisbon-letters",
        title="Lisbon Letters",
        category="Travel",
        rss_url="https://example.substack.com/feed",
        max_episodes=10,
    )
    episodes = fetch_rss_episodes(show)
    assert len(episodes) == 2

    ep1, ep2 = episodes
    assert ep1.audio_url == "https://cdn.substack.com/audio/alfama.mp3?token=abc123&exp=999"
    assert ep1.duration_sec == 750
    assert "Taberna da Rua das Flores" in ep1.content_encoded

    assert ep2.audio_url == "https://cdn.substack.com/audio/chiado.M4A?sig=xyz"
    assert ep2.content_encoded == ""


def test_extract_content_encoded_prefers_html_block():
    entry = {
        "content": [
            {"type": "text/plain", "value": "Short plain text"},
            {"type": "text/html", "value": "<p>Richer HTML version</p>"},
        ]
    }
    assert podcast_index._extract_content_encoded(entry) == "<p>Richer HTML version</p>"
    assert podcast_index._extract_content_encoded({}) == ""


def test_parse_content_encoded_strips_script_style_unescapes_and_splits():
    segments = parse_content_encoded_to_segments(LONG_ESSAY_HTML, duration_sec=0, min_chars=500)
    assert len(segments) == 4

    full_text = " ".join(seg.text for seg in segments)
    assert "console.log" not in full_text
    assert "color: red" not in full_text
    assert "Lisbon’s Alfama District" in segments[0].text
    assert "Taberna da Rua das Flores & order" in segments[2].text
    assert "slate board." in segments[2].text
    assert "\xa0" not in full_text

    # duration_sec=0 -> synthetic 0.0 timestamps
    for seg in segments:
        assert seg.start == 0.0
        assert seg.end == 0.0


def test_parse_content_encoded_proportional_timestamps_and_chunking():
    # Proportional timestamps with duration_sec > 0
    timed = parse_content_encoded_to_segments(LONG_ESSAY_HTML, duration_sec=600, min_chars=500)
    assert len(timed) == 4
    assert timed[0].start == 0.0
    assert timed[-1].end == 600.0
    for idx, seg in enumerate(timed):
        assert seg.start >= 0.0
        assert seg.end >= seg.start
        if idx > 0:
            assert seg.start >= timed[idx - 1].start

    # Oversized single paragraph is chunked to <= 2000 chars per segment
    huge_sentence = "Kyoto temples and tea houses reward slow morning walks along the Philosopher's Path. "
    huge_html = f"<p>{huge_sentence * 60}</p>"
    chunked = parse_content_encoded_to_segments(huge_html, duration_sec=1200, min_chars=500)
    assert len(chunked) >= 3
    assert all(len(seg.text) <= 2000 for seg in chunked)
    assert chunked[0].start == 0.0
    assert chunked[-1].end == 1200.0


def test_parse_content_encoded_min_chars_threshold():
    assert parse_content_encoded_to_segments(None) == []
    assert parse_content_encoded_to_segments("") == []
    assert parse_content_encoded_to_segments(SHORT_SHOW_NOTES_HTML, min_chars=500) == []
    assert len(parse_content_encoded_to_segments(LONG_ESSAY_HTML, min_chars=500)) > 0


def test_is_skippable_episode_respects_free_text_and_always_skips_trailers():
    short_no_text = RawEpisode(
        id="ep-1",
        guid="g-1",
        show_slug="show",
        show_title="Show",
        category="Travel",
        title="Quick Dispatch",
        audio_url="https://example.com/1.mp3",
        duration_sec=240,
        published_at="2026-10-01",
        description="",
        content_encoded=SHORT_SHOW_NOTES_HTML,
    )
    skip, _ = is_skippable_episode(short_no_text, min_duration_sec=600)
    assert skip is True

    short_with_essay = RawEpisode(
        id="ep-2",
        guid="g-2",
        show_slug="show",
        show_title="Show",
        category="Travel",
        title="Lisbon Field Notes",
        audio_url="https://example.com/2.mp3",
        duration_sec=240,
        published_at="2026-10-01",
        description="",
        content_encoded=LONG_ESSAY_HTML,
    )
    skip2, _ = is_skippable_episode(short_with_essay, min_duration_sec=600)
    assert skip2 is False

    short_with_rss_tx = RawEpisode(
        id="ep-3",
        guid="g-3",
        show_slug="show",
        show_title="Show",
        category="Travel",
        title="Short With Transcript",
        audio_url="https://example.com/3.mp3",
        duration_sec=240,
        published_at="2026-10-01",
        description="",
        transcript_url="https://example.com/3.vtt",
    )
    skip3, _ = is_skippable_episode(short_with_rss_tx, min_duration_sec=600)
    assert skip3 is False

    trailer_with_essay = RawEpisode(
        id="ep-4",
        guid="g-4",
        show_slug="show",
        show_title="Show",
        category="Travel",
        title="Season 2 Official Trailer",
        audio_url="https://example.com/4.mp3",
        duration_sec=180,
        published_at="2026-10-01",
        description="",
        transcript_url="https://example.com/4.vtt",
        content_encoded=LONG_ESSAY_HTML,
    )
    skip4, _ = is_skippable_episode(trailer_with_essay, min_duration_sec=600)
    assert skip4 is True


def test_run_transcribe_pass_priority_and_whisper_bypass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shows_csv = tmp_path / "shows.csv"
    shows_csv.write_text(
        "slug,title,category,rss_url,podcast_index_id,max_episodes\n"
        "substack-show,Substack Show,Travel,https://example.substack.com/feed,,10\n",
        encoding="utf-8",
    )
    cache_dir = tmp_path / "cache"

    monkeypatch.setenv("PODATLAS_SHOWS_CSV", str(shows_csv))
    monkeypatch.setenv("PODATLAS_CACHE_DIR", str(cache_dir))

    ep_rss = RawEpisode(
        id="ep-rss",
        guid="guid-rss",
        show_slug="substack-show",
        show_title="Substack Show",
        category="Travel",
        title="Has RSS Transcript And Essay",
        audio_url="https://example.com/rss.mp3",
        duration_sec=1200,
        published_at="2026-10-01",
        description="",
        transcript_url="https://example.com/rss.vtt",
        content_encoded=LONG_ESSAY_HTML,
    )
    ep_substack = RawEpisode(
        id="ep-sub",
        guid="guid-sub",
        show_slug="substack-show",
        show_title="Substack Show",
        category="Travel",
        title="Substack Essay Only",
        audio_url="https://example.com/sub.mp3?token=1",
        duration_sec=420,
        published_at="2026-10-02",
        description="",
        transcript_url=None,
        content_encoded=LONG_ESSAY_HTML,
    )
    ep_whisper = RawEpisode(
        id="ep-whisp",
        guid="guid-whisp",
        show_slug="substack-show",
        show_title="Substack Show",
        category="Travel",
        title="Regular Podcast With Short Notes",
        audio_url="https://example.com/whisp.mp3",
        duration_sec=1800,
        published_at="2026-10-03",
        description="",
        transcript_url=None,
        content_encoded=SHORT_SHOW_NOTES_HTML,
    )
    ep_empty_audio = RawEpisode(
        id="ep-no-audio",
        guid="guid-no-audio",
        show_slug="substack-show",
        show_title="Substack Show",
        category="Travel",
        title="No Audio No Text",
        audio_url="",
        duration_sec=1800,
        published_at="2026-10-04",
        description="",
        transcript_url=None,
        content_encoded=SHORT_SHOW_NOTES_HTML,
    )

    monkeypatch.setattr(
        cli,
        "fetch_rss_episodes",
        lambda show: [ep_rss, ep_substack, ep_whisper, ep_empty_audio],
    )
    monkeypatch.setattr(
        cli,
        "fetch_transcript_if_available",
        lambda url: [TranscriptSegment(start=0.0, end=5.0, text="From RSS tag.")] if url else None,
    )

    whisper_calls: list[str] = []

    class FakeTranscriber:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def transcribe_audio_url(self, audio_url: str) -> list[TranscriptSegment]:
            whisper_calls.append(audio_url)
            return [TranscriptSegment(start=0.0, end=10.0, text="From Whisper.")]

    monkeypatch.setattr(cli, "WhisperTranscriber", FakeTranscriber)

    stats = cli.run_transcribe_pass()

    assert stats["cachedFromRss"] == 1
    assert stats["cachedFromContentEncoded"] == 1
    assert stats["transcribedWithWhisper"] == 1
    assert stats["skippedFiltered"] == 0
    assert whisper_calls == ["https://example.com/whisp.mp3"]

    cached_by_id = {item.id: item for item in cli.load_cached_transcripts(cache_dir)}
    assert set(cached_by_id.keys()) == {"ep-rss", "ep-sub", "ep-whisp"}
    assert cached_by_id["ep-rss"].transcriptSource == "rss_tag"
    assert cached_by_id["ep-sub"].transcriptSource == "content_encoded"
    assert len(cached_by_id["ep-sub"].segments) == 4
    assert cached_by_id["ep-whisp"].transcriptSource == "faster_whisper"


def test_check_show_feed_reports_with_content_encoded(monkeypatch: pytest.MonkeyPatch):
    items_xml = f"""
    <item>
      <title>Full Essay Episode</title>
      <guid>g1</guid>
      <enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" />
      <content:encoded><![CDATA[{LONG_ESSAY_HTML}]]></content:encoded>
    </item>
    <item>
      <title>Short Show Notes Episode</title>
      <guid>g2</guid>
      <enclosure url="https://cdn.example.com/2.mp3" type="audio/mpeg" />
      <content:encoded><![CDATA[{SHORT_SHOW_NOTES_HTML}]]></content:encoded>
    </item>
    """
    _mock_httpx_get(monkeypatch, _make_rss_xml(items_xml))

    show = ShowConfig(
        slug="lisbon-letters",
        title="Lisbon Letters",
        category="Travel",
        rss_url="https://example.substack.com/feed",
    )
    result = check_show_feed(show)
    assert result["ok"] is True
    assert result["episodesFound"] == 2
    assert result["withContentEncoded"] == 1

    # Error path still includes withContentEncoded=0
    monkeypatch.setattr(
        podcast_index,
        "fetch_rss_episodes",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    err_result = check_show_feed(show)
    assert err_result["ok"] is False
    assert err_result["withContentEncoded"] == 0


def test_f4_models_and_config_backward_compatibility(monkeypatch: pytest.MonkeyPatch):
    # EpisodeRecord defaults
    ep = EpisodeRecord(
        id="ep-1",
        show="Show",
        title="Title",
        category="Travel",
        durationSec=600,
        publishedAt="2026-10-01",
        summary="Summary",
    )
    assert ep.source == "podcast"
    assert EpisodeRecord(**ep.model_dump(exclude={"source"}), source="nps").source == "nps"
    assert EpisodeRecord(**ep.model_dump(exclude={"source"}), source="substack").source == "substack"

    # BookRecord defaults for cold-start Wikidata books & timestampSec >= 0 validation
    wd_book = BookRecord(
        title="The Book of Disquiet",
        author="Fernando Pessoa",
        wikidataId="Q1194127",
        source="wikidata",
    )
    assert wd_book.episodeId == ""
    assert wd_book.show == ""
    assert wd_book.timestampSec == 0
    assert wd_book.wikidataId == "Q1194127"
    assert wd_book.source == "wikidata"

    with pytest.raises(pydantic.ValidationError):
        BookRecord(title="Bad", author="Author", timestampSec=-1)

    # PlaceOrGearRecord defaults for standalone NPS/UNESCO places & timestampSec >= 0 validation
    nps_place = PlaceOrGearRecord(
        name="Yosemite Valley",
        type="Landmark",
        note="Granite cliffs.",
        isUnesco=True,
        unescoId="308",
        unescoName="Yosemite National Park",
        source="nps",
    )
    assert nps_place.episodeId == ""
    assert nps_place.timestampSec == 0
    assert nps_place.isUnesco is True
    assert nps_place.unescoId == "308"
    assert nps_place.unescoName == "Yosemite National Park"
    assert nps_place.source == "nps"

    with pytest.raises(pydantic.ValidationError):
        PlaceOrGearRecord(name="Bad", type="Landmark", note="x", timestampSec=-5)

    # CachedEpisodeTranscript accepts content_encoded and round-trips
    tx = CachedEpisodeTranscript(
        id="sub-1",
        guid="g-sub-1",
        showSlug="lisbon",
        showTitle="Lisbon Letters",
        category="Travel",
        title="Alfama",
        audioUrl="https://example.com/a.mp3",
        durationSec=300,
        publishedAt="2026-10-01",
        transcriptSource="content_encoded",
        segments=[TranscriptSegment(start=0.0, end=0.0, text="Alfama text.")],
    )
    roundtripped = CachedEpisodeTranscript.model_validate_json(tx.model_dump_json())
    assert roundtripped.transcriptSource == "content_encoded"

    # Legacy CityData JSON without any new F4 keys validates cleanly
    legacy_payload = {
        "id": "lisbon-portugal",
        "name": "Lisbon",
        "country": "Portugal",
        "lat": 38.7223,
        "lon": -9.1393,
        "primaryCategory": "Travel",
        "tagline": "Seven hills and tiles.",
        "episodes": [
            {
                "id": "ep-legacy",
                "show": "Old Show",
                "title": "Old Ep",
                "category": "Travel",
                "audioUrl": "https://example.com/old.mp3",
                "durationSec": 1200,
                "publishedAt": "2026-01-01",
                "summary": "Legacy summary",
                "mentions": [],
            }
        ],
        "books": [
            {
                "title": "Night Train to Lisbon",
                "author": "Pascal Mercier",
                "episodeId": "ep-legacy",
                "show": "Old Show",
                "timestampSec": 120,
            }
        ],
        "placesAndGear": [
            {
                "name": "Belem Tower",
                "type": "Landmark",
                "note": "Historic fortress",
                "episodeId": "ep-legacy",
                "timestampSec": 340,
            }
        ],
    }
    city = CityData.model_validate_json(json.dumps(legacy_payload))
    assert city.episodes[0].source == "podcast"
    assert city.books[0].source == "podcast"
    assert city.books[0].wikidataId is None
    assert city.placesAndGear[0].isUnesco is False
    assert city.placesAndGear[0].unescoId is None
    assert city.placesAndGear[0].unescoName is None
    assert city.placesAndGear[0].source == "podcast"

    # PipelineSettings nps_api_key env loading
    monkeypatch.delenv("PODATLAS_NPS_API_KEY", raising=False)
    assert PipelineSettings().nps_api_key == ""
    monkeypatch.setenv("PODATLAS_NPS_API_KEY", "test-nps-key-123")
    assert PipelineSettings().nps_api_key == "test-nps-key-123"
