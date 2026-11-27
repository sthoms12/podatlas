"""Podcast Index API and RSS feed ingestion with <podcast:transcript> support."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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
        for enc in entry.get("enclosures", []):
            if "audio" in (enc.get("type") or "") or (enc.get("href") or "").endswith(".mp3"):
                audio_url = enc.get("href", "")
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


_SKIP_TITLE_RE = re.compile(
    r"\b(trailer|teaser|introducing|bonus\s+clip|feed\s+drop|announcement|promo)\b",
    flags=re.IGNORECASE,
)


def is_skippable_episode(episode: RawEpisode, min_duration_sec: int = 600) -> tuple[bool, str]:
    """Return (True, reason) for short clips, trailers, or promos to save GPU credits."""
    if episode.duration_sec > 0 and episode.duration_sec < min_duration_sec:
        return True, f"duration {episode.duration_sec}s < {min_duration_sec}s"
    if _SKIP_TITLE_RE.search(episode.title):
        return True, "title matches trailer/promo pattern"
    return False, ""


def check_show_feed(show: ShowConfig, timeout: float = 15.0) -> dict[str, Any]:
    """Check RSS feed health, enclosure count, and Podcasting 2.0 transcript/location tags."""
    try:
        episodes = fetch_rss_episodes(show, timeout=timeout)
        with_transcripts = sum(1 for ep in episodes if ep.transcript_url)
        with_locations = sum(1 for ep in episodes if ep.subject_location)
        return {
            "slug": show.slug,
            "title": show.title,
            "ok": True,
            "episodesFound": len(episodes),
            "withFreeTranscript": with_transcripts,
            "withSubjectLocation": with_locations,
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
            "error": str(exc),
        }
