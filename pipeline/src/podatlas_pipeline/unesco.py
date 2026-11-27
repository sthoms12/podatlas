"""UNESCO World Heritage Open GeoJSON loader and spatial/name matcher."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .geocode import haversine_km, normalize_key

UNESCO_WHC_GEOJSON_URL = (
    "https://data.unesco.org/api/explore/v2.1/catalog/datasets/whc001/exports/geojson"
)
UNESCO_CACHE_FILENAME = "unesco_whc.geojson"
USER_AGENT = "PodAtlas/0.2 (+https://github.com/sthoms12/podatlas)"

_FUNCTION_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "of",
        "and",
        "in",
        "at",
        "on",
        "to",
        "for",
        "with",
        "its",
        "from",
        "by",
        "de",
        "da",
        "do",
        "du",
        "la",
        "le",
        "les",
        "el",
        "los",
        "las",
        "del",
        "des",
        "di",
        "al",
        "et",
        "en",
    }
)

_GENERIC_WORDS: frozenset[str] = _FUNCTION_STOPWORDS | frozenset(
    {
        "historic",
        "historical",
        "centre",
        "center",
        "monument",
        "monuments",
        "ancient",
        "old",
        "new",
        "city",
        "cities",
        "town",
        "village",
        "site",
        "sites",
        "national",
        "park",
        "parks",
        "sanctuary",
        "area",
        "region",
        "zone",
        "archaeological",
        "cultural",
        "natural",
        "landscape",
        "reserve",
        "district",
        "quarter",
        "banks",
        "river",
        "island",
        "islands",
        "mount",
        "mountain",
        "valley",
        "castle",
        "temple",
        "shrine",
        "palace",
        "church",
        "cathedral",
        "abbey",
        "monastery",
        "basilica",
        "mosque",
        "museum",
        "tower",
        "station",
        "bridge",
        "square",
        "market",
        "street",
        "road",
        "garden",
        "gardens",
        "fort",
        "fortress",
        "wall",
        "walls",
        "gate",
        "hall",
        "house",
        "building",
        "complex",
        "ruins",
        "shop",
        "cafe",
        "coffee",
        "restaurant",
        "bar",
        "hotel",
        "ramen",
        "sushi",
        "bakery",
        "bistro",
        "pub",
        "inn",
    }
)

_UMBRELLA_MARKERS: frozenset[str] = frozenset(
    {
        "historic",
        "historical",
        "monument",
        "monuments",
        "centre",
        "center",
        "ancient",
        "banks",
        "old",
        "cities",
    }
)

# Cross-language monument-type equivalents for component names (e.g. "Nijo Castle" <-> "Nijo-jo")
_COMPONENT_SUFFIX_EQUIVALENTS: dict[str, frozenset[str]] = {
    "castle": frozenset({"jo", "jō", "schloss", "chateau", "castillo", "castello"}),
    "temple": frozenset({"ji", "dera", "tera", "wat", "mandir"}),
    "shrine": frozenset({"jinja", "taisha", "jingu", "gu"}),
    "palace": frozenset({"gosho", "palais", "palacio", "palazzo"}),
}


@dataclass(frozen=True)
class UnescoSite:
    unesco_id: str
    name: str
    category: str
    lat: float
    lon: float
    iso_codes: tuple[str, ...]
    date_inscribed: str = ""
    component_name: str = ""


def _parse_iso_codes(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, list):
        codes = [str(c).strip().upper() for c in raw if str(c).strip()]
        return tuple(dict.fromkeys(codes))
    if isinstance(raw, str):
        codes = [c.strip().upper() for c in re.split(r"[,;\s]+", raw) if c.strip()]
        return tuple(dict.fromkeys(codes))
    return ()


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN check
        return None
    return num


def _extract_feature_coords(props: dict[str, Any], geometry: dict[str, Any] | None) -> tuple[float, float] | None:
    geo_point = props.get("geo_point_2d")
    if isinstance(geo_point, dict):
        lat = _parse_float(geo_point.get("lat"))
        lon = _parse_float(geo_point.get("lon"))
        if lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return (round(lat, 5), round(lon, 5))

    if not isinstance(geometry, dict):
        return None
    geom_type = str(geometry.get("type") or "")
    coords = geometry.get("coordinates")
    if geom_type == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
        lon = _parse_float(coords[0])
        lat = _parse_float(coords[1])
        if lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return (round(lat, 5), round(lon, 5))
    elif geom_type == "MultiPoint" and isinstance(coords, list):
        for pt in coords:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                lon = _parse_float(pt[0])
                lat = _parse_float(pt[1])
                if lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    return (round(lat, 5), round(lon, 5))
    return None


def _strip_parentheticals(text: str) -> str:
    return re.sub(r"\([^)]*\)", " ", text)


def _score_name_match(query_norm: str, target_raw: str, *, is_component: bool) -> tuple[int, float] | None:
    """Return (match_tier, score) if query matches target, or None to reject."""
    target_norm = normalize_key(target_raw)
    if not target_norm:
        return None

    # Exact normalized match
    if query_norm == target_norm:
        return (4 if is_component else 3, 1.0)

    # Strip parenthetical city/region lists on site names before token comparison
    target_clean = normalize_key(_strip_parentheticals(target_raw)) if not is_component else target_norm
    if query_norm == target_clean and target_clean:
        return (4 if is_component else 3, 0.98)

    q_tokens = [t for t in query_norm.split() if len(t) > 1]
    t_tokens = [t for t in target_clean.split() if len(t) > 1]
    if not q_tokens or not t_tokens:
        return None

    q_content = [t for t in q_tokens if t not in _FUNCTION_STOPWORDS]
    t_content = [t for t in t_tokens if t not in _FUNCTION_STOPWORDS]
    if not q_content or not t_content:
        return None

    q_distinctive = [t for t in q_content if t not in _GENERIC_WORDS]
    t_distinctive = [t for t in t_content if t not in _GENERIC_WORDS]
    if not q_distinctive or not t_distinctive:
        return None

    q_content_set = set(q_content)
    t_content_set = set(t_content)
    q_dist_set = set(q_distinctive)
    t_dist_set = set(t_distinctive)

    overlap_dist = q_dist_set & t_dist_set
    if not overlap_dist:
        return None

    overlap_content = set(q_content_set & t_content_set)

    # Allow cross-language monument suffix equivalence on component names
    # (e.g. "Nijo Castle" vs component "Nijo-jo", "Kiyomizu Temple" vs "Kiyomizu-dera").
    if is_component:
        for q_tok in q_content_set - overlap_content:
            equivalents = _COMPONENT_SUFFIX_EQUIVALENTS.get(q_tok)
            if equivalents and (equivalents & t_content_set):
                overlap_content.add(q_tok)

    query_coverage = len(overlap_content) / len(q_content_set)
    distinctive_coverage = len(overlap_dist) / len(q_dist_set)

    if query_coverage < 0.6 or distinctive_coverage < 0.6:
        return None

    # Extra anti-false-positive guard for broad umbrella site names
    # (e.g. "Historic Monuments of Ancient Kyoto", "Historic Centre of Rome", "Paris, Banks of the Seine").
    is_umbrella = (not is_component) and bool(t_content_set & _UMBRELLA_MARKERS)
    if is_umbrella:
        # Reject single-token city-name-only overlap against multi-token umbrella sites
        if len(overlap_dist) < 2 and len(overlap_content) < 2:
            return None

    tier = 2 if is_component else 1
    score = 0.5 * query_coverage + 0.5 * distinctive_coverage
    return (tier, score)


class UnescoIndex:
    """In-memory index of UNESCO World Heritage Sites and their serial components."""

    def __init__(self, sites: list[UnescoSite]) -> None:
        self._sites: list[UnescoSite] = list(sites)

    def __len__(self) -> int:
        return len(self._sites)

    @property
    def sites(self) -> list[UnescoSite]:
        return list(self._sites)

    @classmethod
    def from_geojson_dict(cls, data: dict[str, Any]) -> "UnescoIndex":
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list):
            return cls([])

        sites: list[UnescoSite] = []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties")
            if not isinstance(props, dict):
                continue

            raw_id = (
                props.get("id_no")
                or props.get("unesco_id")
                or props.get("uuid")
                or feature.get("id")
                or ""
            )
            unesco_id = str(raw_id).strip()
            name = str(
                props.get("name_en")
                or props.get("name")
                or props.get("site_name_en")
                or ""
            ).strip()
            category = str(props.get("category") or props.get("category_short") or "").strip()
            date_inscribed = str(props.get("date_inscribed") or "").strip()
            iso_codes = _parse_iso_codes(props.get("iso_codes") or props.get("iso_code"))

            top_coords = _extract_feature_coords(props, feature.get("geometry"))
            if name and top_coords is not None:
                sites.append(
                    UnescoSite(
                        unesco_id=unesco_id,
                        name=name,
                        category=category,
                        lat=top_coords[0],
                        lon=top_coords[1],
                        iso_codes=iso_codes,
                        date_inscribed=date_inscribed,
                        component_name="",
                    )
                )

            raw_components = props.get("components_list")
            components: list[Any] = []
            if isinstance(raw_components, str) and raw_components.strip():
                try:
                    parsed = json.loads(raw_components)
                    if isinstance(parsed, list):
                        components = parsed
                except Exception:
                    components = []
            elif isinstance(raw_components, list):
                components = raw_components

            for comp in components:
                if not isinstance(comp, dict):
                    continue
                comp_name = str(comp.get("name") or "").strip()
                comp_lat = _parse_float(comp.get("latitude") if "latitude" in comp else comp.get("lat"))
                comp_lon = _parse_float(comp.get("longitude") if "longitude" in comp else comp.get("lon"))
                if (
                    not comp_name
                    or comp_lat is None
                    or comp_lon is None
                    or not (-90.0 <= comp_lat <= 90.0)
                    or not (-180.0 <= comp_lon <= 180.0)
                ):
                    continue
                sites.append(
                    UnescoSite(
                        unesco_id=unesco_id,
                        name=name or comp_name,
                        category=category,
                        lat=round(comp_lat, 5),
                        lon=round(comp_lon, 5),
                        iso_codes=iso_codes,
                        date_inscribed=date_inscribed,
                        component_name=comp_name,
                    )
                )

        return cls(sites)

    @classmethod
    def from_geojson_file(cls, path: Path) -> "UnescoIndex":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_geojson_dict(data)

    @classmethod
    def download_and_load(
        cls,
        cache_dir: Path,
        max_age_days: float = 30.0,
        timeout: float = 45.0,
    ) -> "UnescoIndex":
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "unesco_whc.geojson"

        if cache_path.exists():
            age_sec = time.time() - cache_path.stat().st_mtime
            if age_sec <= max_age_days * 86400.0:
                try:
                    return cls.from_geojson_file(cache_path)
                except Exception:
                    pass

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(
                    UNESCO_WHC_GEOJSON_URL,
                    headers={"User-Agent": USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("features"), list):
                raise ValueError("Invalid UNESCO GeoJSON payload")
            cache_path.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
            return cls.from_geojson_dict(data)
        except Exception:
            if cache_path.exists():
                try:
                    return cls.from_geojson_file(cache_path)
                except Exception:
                    pass
            return cls([])

    def match(
        self,
        place_name: str,
        city_lat: float,
        city_lon: float,
        country_code: str = "",
        max_distance_km: float = 60.0,
    ) -> UnescoSite | None:
        query_norm = normalize_key(place_name)
        if not query_norm:
            return None

        cc_upper = country_code.strip().upper()
        best_rank: tuple[int, float, float] | None = None
        best_site: UnescoSite | None = None

        for site in self._sites:
            if cc_upper and site.iso_codes and cc_upper not in site.iso_codes:
                continue
            dist_km = haversine_km(city_lat, city_lon, site.lat, site.lon)
            if dist_km > max_distance_km:
                continue

            if site.component_name:
                scored = _score_name_match(query_norm, site.component_name, is_component=True)
            else:
                scored = _score_name_match(query_norm, site.name, is_component=False)

            if scored is None:
                continue

            tier, score = scored
            rank = (tier, score, -dist_km)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_site = site

        return best_site
