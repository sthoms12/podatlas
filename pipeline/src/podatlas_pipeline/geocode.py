"""Offline GeoNames cities5000.txt geocoder and canonical city slug generator."""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

GEONAMES_CITIES5000_URL = "https://download.geonames.org/export/dump/cities5000.zip"

# ISO-3166 alpha-2 -> display country name for common travel destinations
COUNTRY_NAMES: dict[str, str] = {
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CL": "Chile",
    "CN": "China",
    "CO": "Colombia",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "EG": "Egypt",
    "ES": "Spain",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HR": "Croatia",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IS": "Iceland",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "MA": "Morocco",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PE": "Peru",
    "PL": "Poland",
    "PT": "Portugal",
    "SE": "Sweden",
    "SG": "Singapore",
    "TH": "Thailand",
    "TR": "Turkey",
    "TW": "Taiwan",
    "US": "USA",
    "VN": "Vietnam",
    "ZA": "South Africa",
}


def normalize_key(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def slugify_city(city: str, country: str) -> str:
    raw = f"{city} {country}".strip()
    return normalize_key(raw).replace(" ", "-")


@dataclass(frozen=True)
class GeoCity:
    id: str
    name: str
    country: str
    country_code: str
    lat: float
    lon: float
    population: int


class GeoNamesIndex:
    """Offline population-weighted city lookup built from GeoNames cities5000.txt."""

    def __init__(self, cities: list[GeoCity], aliases: dict[str, list[GeoCity]] | None = None) -> None:
        self._by_key: dict[str, list[GeoCity]] = {}
        for city in cities:
            for key in {normalize_key(city.name)}:
                if key:
                    self._by_key.setdefault(key, []).append(city)
        if aliases:
            for key, matches in aliases.items():
                self._by_key.setdefault(key, []).extend(matches)
        for matches in self._by_key.values():
            matches.sort(key=lambda c: c.population, reverse=True)

    @classmethod
    def from_geonames_file(cls, txt_path: Path) -> "GeoNamesIndex":
        cities: list[GeoCity] = []
        aliases: dict[str, list[GeoCity]] = {}
        with txt_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 15:
                    continue
                name = parts[1].strip()
                asciiname = parts[2].strip()
                alternatenames = parts[3].strip()
                lat = float(parts[4])
                lon = float(parts[5])
                country_code = parts[8].strip().upper()
                population = int(parts[14] or 0)
                country = COUNTRY_NAMES.get(country_code, country_code)
                city = GeoCity(
                    id=slugify_city(asciiname or name, country),
                    name=name,
                    country=country,
                    country_code=country_code,
                    lat=round(lat, 4),
                    lon=round(lon, 4),
                    population=population,
                )
                cities.append(city)
                for alt in filter(None, [asciiname, *alternatenames.split(",")[:8]]):
                    k = normalize_key(alt)
                    if k and k != normalize_key(name):
                        aliases.setdefault(k, []).append(city)
        return cls(cities, aliases)

    @classmethod
    def download_and_load(cls, cache_dir: Path) -> "GeoNamesIndex":
        cache_dir.mkdir(parents=True, exist_ok=True)
        txt_path = cache_dir / "cities5000.txt"
        if not txt_path.exists():
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(GEONAMES_CITIES5000_URL)
                resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                txt_path.write_bytes(zf.read("cities5000.txt"))
        return cls.from_geonames_file(txt_path)

    def lookup(self, city_name: str, country_hint: str = "") -> GeoCity | None:
        key = normalize_key(city_name)
        candidates = self._by_key.get(key)
        if not candidates:
            return None
        if not country_hint:
            return candidates[0]

        hint_norm = normalize_key(country_hint)
        for cand in candidates:
            if (
                normalize_key(cand.country) == hint_norm
                or cand.country_code.lower() == hint_norm
                or (hint_norm in {"uk", "scotland", "england", "wales"} and cand.country_code == "GB")
                or (hint_norm in {"united states", "us", "usa"} and cand.country_code == "US")
            ):
                return cand
        return candidates[0]
