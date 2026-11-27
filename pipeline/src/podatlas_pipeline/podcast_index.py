"""Podcast Index API and RSS feed ingestion with <podcast:transcript> support."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import feedparser
import httpx

from .models import Category, TranscriptSegment

PODCAST_INDEX_BASE = "https://api.podcastindex.org/api/1.0"
PODCAST_NS = "{https://podcastindex.org/namespace/1.0}"


@dataclass
class ShowConfig:
    slug: str
    title: str
    category: Category
    rss_url: str
    podcast_index_id: str = ""
    max_episodes: int = 15


@dataclass
class RawEpisode:
    id: str
    guid: str
    show_slug: str
    show_title: str
    category: Category
    title: str
    audio_url: str
    duration_sec: int
    published_at: str
    description: str
    transcript_url: str | None = None
    transcript_type: str | None = None
    subject_location: str | None = None
    content_encoded: str = ""


def load_shows_csv(path: Path) -> list[ShowConfig]:
    shows: list[ShowConfig] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            shows.append(
                ShowConfig(
                    slug=row["slug"].strip(),
                    title=row["title"].strip(),
                    category=row["category"].strip(),  # type: ignore[arg-type]
                    rss_url=row["rss_url"].strip(),
                    podcast_index_id=(row.get("podcast_index_id") or "").strip(),
                    max_episodes=int(row.get("max_episodes") or 15),
                )
            )
    return shows


def podcast_index_headers(api_key: str, api_secret: str) -> dict[str, str]:
    now = str(int(time.time()))
    token = hashlib.sha1(f"{api_key}{api_secret}{now}".encode("utf-8")).hexdigest()
    return {
        "User-Agent": "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)",
        "X-Auth-Date": now,
        "X-Auth-Key": api_key,
        "Authorization": token,
    }


def _parse_duration(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    parts = [int(p) for p in text.split(":") if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0


def _make_episode_id(show_slug: str, guid: str) -> str:
    digest = hashlib.sha1(f"{show_slug}:{guid}".encode("utf-8")).hexdigest()[:10]
    return f"{show_slug[:6]}-{digest}"


def _extract_transcript_link(entry: Any) -> tuple[str | None, str | None]:
    # feedparser exposes <podcast:transcript> either via podcast_transcript or links
    transcript = entry.get("podcast_transcript")
    if isinstance(transcript, dict) and transcript.get("url"):
        return transcript["url"], transcript.get("type")
    if isinstance(transcript, list):
        for item in transcript:
            if isinstance(item, dict) and item.get("url"):
                return item["url"], item.get("type")
    for link in entry.get("links", []):
        if link.get("rel") == "transcript" or "transcript" in (link.get("type") or ""):
            return link.get("href"), link.get("type")
    return None, None


def _pick_subject_location(elements: list[ET.Element]) -> str | None:
    """Return the best <podcast:location> text, preferring rel='subject' and ignoring rel='creator'."""
    fallback: str | None = None
    for el in elements:
        rel = (el.attrib.get("rel") or "").strip().lower()
        name = (el.text or "").strip()
        if not name or rel == "creator":
            continue
        country = (el.attrib.get("country") or "").strip().upper()
        formatted = f"{name}, {country}" if country and country.lower() not in name.lower() else name
        if rel == "subject":
            return formatted
        if not rel and fallback is None:
            fallback = formatted
    return fallback


def parse_podcast_locations(xml_text: str) -> tuple[str | None, dict[str, str]]:
    """Extract channel-level and item-level <podcast:location> subject tags from RSS XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, {}

    channel = root.find("channel")
    if channel is None:
        return None, {}

    def _location_children(parent: ET.Element) -> list[ET.Element]:
        return [
            child
            for child in parent
            if child.tag == f"{PODCAST_NS}location" or child.tag.endswith("}location")
        ]

    channel_location = _pick_subject_location(_location_children(channel))
    item_locations: dict[str, str] = {}
    for item in channel.findall("item"):
        loc = _pick_subject_location(_location_children(item))
        if not loc:
            continue
        guid_el = item.find("guid")
        title_el = item.find("title")
        if guid_el is not None and (guid_el.text or "").strip():
            item_locations[(guid_el.text or "").strip()] = loc
        if title_el is not None and (title_el.text or "").strip():
            item_locations[(title_el.text or "").strip()] = loc

    return channel_location, item_locations


_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".wav", ".opus")


def _extract_content_encoded(entry: Any) -> str:
    """Extract <content:encoded> (or Atom <content>) HTML from a feedparser entry."""
    content = entry.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    html_blocks: list[str] = []
    other_blocks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        value = (block.get("value") or "").strip()
        if not value:
            continue
        ctype = (block.get("type") or "").lower()
        if "html" in ctype or "xhtml" in ctype:
            html_blocks.append(value)
        else:
            other_blocks.append(value)

    if html_blocks:
        return max(html_blocks, key=len)
    if other_blocks:
        return max(other_blocks, key=len)
    return ""


def fetch_rss_episodes(show: ShowConfig, timeout: float = 20.0) -> list[RawEpisode]:
    """Fetch recent episodes directly from a podcast's public RSS feed."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(show.rss_url, headers={"User-Agent": "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"})
        resp.raise_for_status()
        xml_text = resp.text
        parsed = feedparser.parse(xml_text)

    channel_location, item_locations = parse_podcast_locations(xml_text)

    episodes: list[RawEpisode] = []
    for entry in parsed.entries[: show.max_episodes]:
        audio_url = ""
        for enc in entry.get("enclosures") or []:
            href = (enc.get("href") or "").strip()
            if not href:
                continue
            enc_type = (enc.get("type") or "").lower()
            path_lower = urlparse(href).path.lower()
            if "audio" in enc_type or path_lower.endswith(_AUDIO_EXTENSIONS):
                audio_url = href
                break
        if not audio_url:
            continue

        guid = entry.get("id") or entry.get("guid") or audio_url
        title = (entry.get("title") or "Untitled Episode").strip()
        pub_struct = entry.get("published_parsed")
        if pub_struct:
            published_at = datetime(*pub_struct[:6], tzinfo=timezone.utc).strftime("%Y-%m-%d")
        else:
            published_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        transcript_url, transcript_type = _extract_transcript_link(entry)
        subject_location = item_locations.get(guid) or item_locations.get(title) or channel_location
        content_encoded = _extract_content_encoded(entry)
        episodes.append(
            RawEpisode(
                id=_make_episode_id(show.slug, guid),
                guid=guid,
                show_slug=show.slug,
                show_title=show.title,
                category=show.category,
                title=title,
                audio_url=audio_url,
                duration_sec=_parse_duration(entry.get("itunes_duration")),
                published_at=published_at,
                description=(entry.get("summary") or "").strip(),
                transcript_url=transcript_url,
                transcript_type=transcript_type,
                subject_location=subject_location,
                content_encoded=content_encoded,
            )
        )
    return episodes


_TS_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})(?:[.,](\d+))?")


def _ts_to_seconds(match: re.Match[str]) -> float:
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int((match.group(4) or "0").ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def parse_srt_or_vtt(content: str) -> list[TranscriptSegment]:
    """Parse SRT or WebVTT transcript text into timestamped segments."""
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\r?\n\r?\n", content.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        ts_line_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_line_idx is None:
            continue
        left, right = lines[ts_line_idx].split("-->", 1)
        m_start = _TS_RE.search(left)
        m_end = _TS_RE.search(right)
        if not m_start or not m_end:
            continue
        text = " ".join(lines[ts_line_idx + 1 :])
        text = re.sub(r"<[^>]+>", "", text).strip()
        if text:
            segments.append(
                TranscriptSegment(
                    start=_ts_to_seconds(m_start),
                    end=_ts_to_seconds(m_end),
                    text=text,
                )
            )
    return segments


def fetch_transcript_if_available(
    transcript_url: str | None,
    timeout: float = 20.0,
) -> list[TranscriptSegment] | None:
    """Download and parse a free <podcast:transcript> file (JSON/SRT/VTT) if present."""
    if not transcript_url:
        return None
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(transcript_url, headers={"User-Agent": "PodAtlas/0.1"})
        resp.raise_for_status()
        body = resp.text

    content_type = (resp.headers.get("content-type") or "").lower()
    if "json" in content_type or transcript_url.lower().endswith(".json"):
        data = json.loads(body)
        raw_segs = data.get("segments", []) if isinstance(data, dict) else data
        segments: list[TranscriptSegment] = []
        for seg in raw_segs:
            start = float(seg.get("startTime", seg.get("start", 0.0)))
            end = float(seg.get("endTime", seg.get("end", start)))
            text = str(seg.get("body", seg.get("text", ""))).strip()
            if text:
                segments.append(TranscriptSegment(start=start, end=end, text=text))
        return segments or None

    parsed = parse_srt_or_vtt(body)
    return parsed or None


_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "tr",
        "section",
        "article",
        "hr",
    }
)
_SKIP_TAGS = frozenset({"script", "style"})
_BLANK_LINE_RE = re.compile(r"\r?\n\s*\r?\n")


class _ContentHTMLParser(HTMLParser):
    """Pure-stdlib HTML-to-paragraphs parser that strips script/style and normalizes text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []
        self._paragraphs: list[str] = []

    def _flush(self) -> None:
        if not self._chunks:
            return
        raw = "".join(self._chunks).replace("\xa0", " ")
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned:
            self._paragraphs.append(cleaned)
        self._chunks = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth == 0 and t in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not data:
            return
        if _BLANK_LINE_RE.search(data):
            parts = _BLANK_LINE_RE.split(data)
            for idx, part in enumerate(parts):
                if idx > 0:
                    self._flush()
                if part:
                    self._chunks.append(part)
        else:
            self._chunks.append(data)

    def get_paragraphs(self) -> list[str]:
        self._flush()
        return self._paragraphs


def _chunk_paragraph(text: str, max_chunk_chars: int = 2000) -> list[str]:
    """Split oversized paragraphs so downstream LLM context caps never drop an entire segment."""
    if len(text) <= max_chunk_chars:
        return [text]

    chunks: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chunk_chars:
            if current:
                chunks.append(current)
                current = ""
            words = sentence.split(" ")
            buf = ""
            for word in words:
                if len(word) > max_chunk_chars:
                    if buf:
                        chunks.append(buf)
                        buf = ""
                    for i in range(0, len(word), max_chunk_chars):
                        chunks.append(word[i : i + max_chunk_chars])
                elif not buf:
                    buf = word
                elif len(buf) + 1 + len(word) <= max_chunk_chars:
                    buf = f"{buf} {word}"
                else:
                    chunks.append(buf)
                    buf = word
            if buf:
                current = buf
        elif not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_chunk_chars:
            current = f"{current} {sentence}"
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def parse_content_encoded_to_segments(
    html_text: str | None,
    duration_sec: int = 0,
    min_chars: int = 500,
) -> list[TranscriptSegment]:
    """Convert RSS <content:encoded> HTML into TranscriptSegment paragraphs when >= min_chars."""
    if not html_text or not html_text.strip():
        return []

    parser = _ContentHTMLParser()
    parser.feed(html_text)
    parser.close()

    paragraphs: list[str] = []
    for para in parser.get_paragraphs():
        paragraphs.extend(_chunk_paragraph(para))

    total_chars = sum(len(p) for p in paragraphs)
    if not paragraphs or total_chars < min_chars:
        return []

    segments: list[TranscriptSegment] = []
    if duration_sec > 0:
        offset = 0
        for para in paragraphs:
            start = round((offset / total_chars) * duration_sec, 3)
            offset += len(para)
            end = max(start, round((offset / total_chars) * duration_sec, 3))
            segments.append(TranscriptSegment(start=start, end=end, text=para))
    else:
        for para in paragraphs:
            segments.append(TranscriptSegment(start=0.0, end=0.0, text=para))
    return segments


_SKIP_TITLE_RE = re.compile(
    r"\b(trailer|teaser|introducing|bonus\s+clip|feed\s+drop|announcement|promo)\b",
    flags=re.IGNORECASE,
)


def is_skippable_episode(episode: RawEpisode, min_duration_sec: int = 600) -> tuple[bool, str]:
    """Return (True, reason) for short clips, trailers, or promos to save GPU credits."""
    if _SKIP_TITLE_RE.search(episode.title):
        return True, "title matches trailer/promo pattern"
    if 0 < episode.duration_sec < min_duration_sec:
        has_free_text = bool(episode.transcript_url) or bool(
            parse_content_encoded_to_segments(episode.content_encoded)
        )
        if not has_free_text:
            return True, f"duration {episode.duration_sec}s < {min_duration_sec}s"
    return False, ""


def check_show_feed(show: ShowConfig, timeout: float = 15.0) -> dict[str, Any]:
    """Check RSS feed health, enclosure count, and Podcasting 2.0 transcript/location tags."""
    try:
        episodes = fetch_rss_episodes(show, timeout=timeout)
        with_transcripts = sum(1 for ep in episodes if ep.transcript_url)
        with_locations = sum(1 for ep in episodes if ep.subject_location)
        with_content_encoded = sum(
            1 for ep in episodes if parse_content_encoded_to_segments(ep.content_encoded)
        )
        return {
            "slug": show.slug,
            "title": show.title,
            "ok": True,
            "episodesFound": len(episodes),
            "withFreeTranscript": with_transcripts,
            "withSubjectLocation": with_locations,
            "withContentEncoded": with_content_encoded,
            "error": None,
        }
    except Exception as exc:
        return {
            "slug": show.slug,
            "title": show.title,
            "ok": False,
            "episodesFound": 0,
            "withFreeTranscript": 0,
            "withSubjectLocation": 0,
            "withContentEncoded": 0,
            "error": str(exc),
        }
