"""National Park Service (NPS) Data API client and PodAtlas CityData mapper.

Consumes public-domain NPS endpoints:
  - /parks  -> CityData (national park / historic site pins)
  - /audio  -> EpisodeRecord (source="nps", public-domain ranger audio tours)
  - /places -> PlaceOrGearRecord (source="nps", geocoded landmarks)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .geocode import normalize_key, slugify_city
from .models import CityData, EpisodeRecord, PlaceOrGearRecord

NPS_API_BASE = "https://developer.nps.gov/api/v1"
USER_AGENT = "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"


def _parse_coord(value: Any, min_val: float, max_val: float) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if not (min_val <= num <= max_val):
        return None
    return round(num, 5)


def _extract_audio_url(item: dict[str, Any]) -> str:
    versions = item.get("versions")
    if isinstance(versions, list):
        mp3_candidate = ""
        fallback = ""
        for ver in versions:
            if not isinstance(ver, dict):
                continue
            url = str(ver.get("url") or "").strip()
            if not url:
                continue
            file_type = str(ver.get("fileType") or "").strip().lower()
            if file_type == "mp3" or url.lower().endswith(".mp3"):
                mp3_candidate = url
                break
            if not fallback:
                fallback = url
        if mp3_candidate:
            return mp3_candidate
        if fallback:
            return fallback

    for key in ("audioUrl", "url"):
        val = str(item.get(key) or "").strip()
        if val.startswith(("http://", "https://")):
            return val
    return ""


def _parse_duration_sec(item: dict[str, Any]) -> int:
    if item.get("durationMs") is not None:
        try:
            return max(0, int(float(item["durationMs"]) // 1000))
        except (TypeError, ValueError):
            pass
    if item.get("durationSec") is not None:
        try:
            return max(0, int(float(item["durationSec"])))
        except (TypeError, ValueError):
            pass
    return 0


def _make_nps_episode_id(park_code: str, raw_id: str) -> str:
    digest = hashlib.sha1(f"nps:{park_code}:{raw_id}".encode("utf-8")).hexdigest()[:10]
    return f"nps-{park_code[:6]}-{digest}"


class NPSClient:
    """Disk-cached HTTP client for the official U.S. National Park Service Data API."""

    def __init__(
        self,
        api_key: str = "",
        cache_dir: Path | None = None,
        base_url: str = NPS_API_BASE,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = (api_key or os.environ.get("NPS_API_KEY", "")).strip()
        self.cache_dir = cache_dir
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _cache_file(self, endpoint: str, params: dict[str, Any]) -> Path | None:
        if not self.cache_dir:
            return None
        key_payload = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
        digest = hashlib.sha1(key_payload.encode("utf-8")).hexdigest()[:16]
        safe_ep = endpoint.strip("/").replace("/", "_")
        return self.cache_dir / f"nps_{safe_ep}_{digest}.json"

    def _get_json(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        clean_params = {k: v for k, v in params.items() if v is not None and str(v).strip() != ""}
        cache_path = self._cache_file(endpoint, clean_params)
        if cache_path and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, list):
                    return cached
            except Exception:
                pass

        if not self.api_key:
            return []

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "X-Api-Key": self.api_key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(url, params=clean_params, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            return []

        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return []

        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def fetch_parks(self, park_code: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if park_code:
            params["parkCode"] = park_code.strip().lower()
        return self._get_json("parks", params)

    def fetch_audio(self, park_code: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if park_code:
            params["parkCode"] = park_code.strip().lower()
        return self._get_json("audio", params)

    def fetch_places(self, park_code: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": max(1, int(limit))}
        if park_code:
            params["parkCode"] = park_code.strip().lower()
        return self._get_json("places", params)


def build_park_city_data(
    park: dict[str, Any],
    audio_items: list[dict[str, Any]],
    place_items: list[dict[str, Any]],
) -> CityData | None:
    """Convert an NPS /parks record plus its /audio and /places payloads into CityData."""
    name = str(park.get("fullName") or park.get("name") or "").strip()
    park_code = str(park.get("parkCode") or "").strip().lower() or "nps"
    lat = _parse_coord(park.get("latitude"), -90.0, 90.0)
    lon = _parse_coord(park.get("longitude"), -180.0, 180.0)
    if not name or lat is None or lon is None:
        return None

    description = str(park.get("description") or "").strip()
    tagline = (
        description[:220].rstrip()
        if description
        else f"Official National Park Service audio tours and landmarks for {name}."
    )
    city_id = slugify_city(name, "USA")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    episodes: list[EpisodeRecord] = []
    seen_ep_ids: set[str] = set()
    for audio in audio_items:
        if not isinstance(audio, dict):
            continue
        audio_url = _extract_audio_url(audio)
        title = str(audio.get("title") or "").strip()
        if not title or not audio_url:
            continue
        raw_id = str(audio.get("id") or audio_url)
        ep_id = _make_nps_episode_id(park_code, raw_id)
        if ep_id in seen_ep_ids:
            continue
        seen_ep_ids.add(ep_id)
        summary = str(audio.get("description") or f"Public-domain NPS audio tour for {name}.").strip()
        episodes.append(
            EpisodeRecord(
                id=ep_id,
                show=f"NPS Audio · {name}",
                title=title,
                category="Travel",
                audioUrl=audio_url,
                durationSec=_parse_duration_sec(audio),
                publishedAt=today,
                summary=summary,
                mentions=[],
                source="nps",
            )
        )

    places: list[PlaceOrGearRecord] = []
    seen_place_keys: set[str] = set()
    for place in place_items:
        if not isinstance(place, dict):
            continue
        place_name = str(place.get("title") or place.get("name") or "").strip()
        if not place_name:
            continue
        pkey = normalize_key(place_name)
        if not pkey or pkey in seen_place_keys:
            continue
        seen_place_keys.add(pkey)
        note = str(
            place.get("listingDescription")
            or place.get("bodyText")
            or f"Official NPS site in {name}."
        ).strip()
        p_lat = _parse_coord(place.get("latitude"), -90.0, 90.0)
        p_lon = _parse_coord(place.get("longitude"), -180.0, 180.0)
        places.append(
            PlaceOrGearRecord(
                name=place_name,
                type="Landmark",
                note=note[:280],
                episodeId="",
                timestampSec=0,
                show="",
                lat=p_lat,
                lon=p_lon,
                endorsement="strong_rec",
                mentionCount=1,
                uniqueShowCount=1,
                mentionedByShows=[],
                source="nps",
            )
        )

    return CityData(
        id=city_id,
        name=name,
        country="USA",
        lat=lat,
        lon=lon,
        primaryCategory="Travel",
        tagline=tagline,
        episodes=episodes,
        books=[],
        placesAndGear=places,
    )


def ingest_nps_parks(
    client: NPSClient,
    park_code: str | None = None,
    limit: int = 25,
    existing_cities: dict[str, CityData] | None = None,
) -> dict[str, CityData]:
    """Fetch NPS parks, audio, and places and idempotently merge into cities_by_id."""
    cities_by_id: dict[str, CityData] = dict(existing_cities) if existing_cities else {}
    parks = client.fetch_parks(park_code=park_code, limit=limit)

    for park in parks:
        code = str(park.get("parkCode") or "").strip().lower()
        audio_items = client.fetch_audio(park_code=code) if code else []
        place_items = client.fetch_places(park_code=code) if code else []
        park_city = build_park_city_data(park, audio_items, place_items)
        if park_city is None:
            continue

        existing = cities_by_id.get(park_city.id)
        if not existing:
            cities_by_id[park_city.id] = park_city
            continue

        existing_ep_ids = {ep.id for ep in existing.episodes}
        for ep in park_city.episodes:
            if ep.id not in existing_ep_ids:
                existing.episodes.append(ep)
                existing_ep_ids.add(ep.id)

        existing_place_keys = {normalize_key(p.name) for p in existing.placesAndGear}
        for place in park_city.placesAndGear:
            pkey = normalize_key(place.name)
            if pkey not in existing_place_keys:
                existing.placesAndGear.append(place)
                existing_place_keys.add(pkey)

    return cities_by_id
