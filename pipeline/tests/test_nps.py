"""Unit tests for the National Park Service (NPS) Data API client and mapper."""

from __future__ import annotations

from pathlib import Path

import httpx

from podatlas_pipeline.nps import NPSClient, build_park_city_data, ingest_nps_parks


def _sample_park() -> dict:
    return {
        "id": "park-yell",
        "parkCode": "yell",
        "fullName": "Yellowstone National Park",
        "name": "Yellowstone",
        "latitude": "44.59824",
        "longitude": "-110.54717",
        "description": "America's first national park, known for geysers and wildlife.",
    }


def _sample_audio() -> list[dict]:
    return [
        {
            "id": "aud-1",
            "title": "Old Faithful Geyser Basin Tour",
            "description": "Ranger audio guide to the upper geyser basin.",
            "durationMs": 420000,
            "versions": [
                {"url": "https://www.nps.gov/av/yell/old-faithful.mp3", "fileType": "mp3"}
            ],
        }
    ]


def _sample_places() -> list[dict]:
    return [
        {
            "id": "pl-1",
            "title": "Old Faithful Geyser",
            "listingDescription": "Iconic geothermal cone geyser.",
            "latitude": "44.4605",
            "longitude": "-110.8281",
        },
        {
            "id": "pl-2",
            "title": "Backcountry Permit Desk",
            "listingDescription": "No coordinates provided.",
            "latitude": "",
            "longitude": "",
        },
    ]


def test_build_park_city_data_maps_audio_and_places() -> None:
    city = build_park_city_data(_sample_park(), _sample_audio(), _sample_places())
    assert city is not None
    assert city.id == "yellowstone-national-park-usa"
    assert city.country == "USA"
    assert city.primaryCategory == "Travel"
    assert len(city.episodes) == 1
    assert city.episodes[0].source == "nps"
    assert city.episodes[0].durationSec == 420
    assert city.episodes[0].audioUrl == "https://www.nps.gov/av/yell/old-faithful.mp3"

    assert len(city.placesAndGear) == 2
    assert city.placesAndGear[0].source == "nps"
    assert city.placesAndGear[0].episodeId == ""
    assert city.placesAndGear[0].lat == 44.4605
    assert city.placesAndGear[0].lon == -110.8281
    assert city.placesAndGear[1].lat is None
    assert city.placesAndGear[1].lon is None


def test_nps_client_caches_responses_and_degrades_on_error(tmp_path: Path, monkeypatch) -> None:
    calls = {"count": 0}

    class FakeResp:
        def __init__(self, status: int, data: dict):
            self.status_code = status
            self._data = data

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]

        def json(self) -> dict:
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str, params=None, headers=None):
            calls["count"] += 1
            if url.endswith("/parks"):
                return FakeResp(200, {"data": [_sample_park()]})
            if url.endswith("/audio"):
                return FakeResp(200, {"data": _sample_audio()})
            if url.endswith("/places"):
                return FakeResp(500, {})
            return FakeResp(404, {})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = NPSClient(api_key="test-key", cache_dir=tmp_path / "nps_cache")

    parks1 = client.fetch_parks(park_code="yell", limit=1)
    parks2 = client.fetch_parks(park_code="yell", limit=1)
    assert len(parks1) == 1
    assert len(parks2) == 1
    assert calls["count"] == 1  # second call served from disk cache

    # HTTP 500 degrades gracefully to empty list
    places = client.fetch_places(park_code="yell")
    assert places == []

    # Idempotent merge
    cities = ingest_nps_parks(client, park_code="yell", limit=1)
    cities_again = ingest_nps_parks(client, park_code="yell", limit=1, existing_cities=cities)
    assert len(cities_again) == 1
    city = next(iter(cities_again.values()))
    assert len(city.episodes) == 1
