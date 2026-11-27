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

# Full ISO-3166 alpha-2 -> display country name for global destination geocoding
COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Aland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "Saint Barthelemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Bonaire",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos Islands",
    "CD": "DR Congo",
    "CF": "Central African Republic",
    "CG": "Republic of the Congo",
    "CH": "Switzerland",
    "CI": "Ivory Coast",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cabo Verde",
    "CW": "Curacao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "Saint Kitts and Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "Saint Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "Saint Martin",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MN": "Mongolia",
    "MO": "Macao",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "Saint Pierre and Miquelon",
    "PN": "Pitcairn Islands",
    "PR": "Puerto Rico",
    "PS": "Palestine",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Reunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "Saint Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard and Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "Sao Tome and Principe",
    "SV": "El Salvador",
    "SX": "Sint Maarten",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UM": "US Minor Outlying Islands",
    "US": "USA",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Vatican City",
    "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "US Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis and Futuna",
    "WS": "Samoa",
    "XK": "Kosovo",
    "YE": "Yemen",
    "YT": "Mayotte",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers between two WGS84 coordinates."""
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class PlaceGeocoder:
    """Cached OSM Nominatim geocoder for city-scoped landmarks and restaurants."""

    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

    def __init__(
        self,
        cache_path: Path | None = None,
        max_distance_km: float = 60.0,
        rate_limit_sec: float = 1.1,
        timeout: float = 12.0,
    ) -> None:
        self.cache_path = cache_path
        self.max_distance_km = max_distance_km
        self.rate_limit_sec = rate_limit_sec
        self.timeout = timeout
        self._last_request_ts = 0.0
        self._cache: dict[str, list[float] | None] = {}
        if cache_path and cache_path.exists():
            import json

            try:
                self._cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        import json

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def geocode(
        self,
        place_name: str,
        city_name: str,
        country: str,
        city_lat: float,
        city_lon: float,
    ) -> tuple[float, float] | None:
        """Geocode a landmark or restaurant, validating it lies near the target city."""
        import time

        key = normalize_key(f"{place_name} {city_name} {country}")
        if not key:
            return None
        if key in self._cache:
            cached = self._cache[key]
            return (cached[0], cached[1]) if cached else None

        elapsed = time.monotonic() - self._last_request_ts
        if self._last_request_ts > 0 and elapsed < self.rate_limit_sec:
            time.sleep(self.rate_limit_sec - elapsed)

        query = f"{place_name}, {city_name}, {country}".strip(", ")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    self.NOMINATIM_URL,
                    params={"q": query, "format": "jsonv2", "limit": 3},
                    headers={"User-Agent": "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"},
                )
                self._last_request_ts = time.monotonic()
                resp.raise_for_status()
                results = resp.json()
        except Exception:
            return None

        best: tuple[float, float] | None = None
        for hit in results:
            try:
                lat = round(float(hit["lat"]), 5)
                lon = round(float(hit["lon"]), 5)
            except (KeyError, TypeError, ValueError):
                continue
            if haversine_km(city_lat, city_lon, lat, lon) <= self.max_distance_km:
                best = (lat, lon)
                break

        self._cache[key] = [best[0], best[1]] if best else None
        self._save_cache()
        return best
