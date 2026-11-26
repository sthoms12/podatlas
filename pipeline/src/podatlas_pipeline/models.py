"""Pydantic data models defining the PodAtlas static JSON contract."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

Category = Literal["Travel", "History", "Food", "Culture"]
MentionType = Literal["book", "place", "gear"]
PlaceType = Literal["Landmark", "Food", "Gear"]
SearchKind = Literal["City", "Episode", "Book", "Landmark", "Food", "Gear"]
EndorsementLevel = Literal["strong_rec", "casual_mention"]


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str


class EpisodeMention(BaseModel):
    sec: int = Field(ge=0)
    type: MentionType
    label: str


class EpisodeRecord(BaseModel):
    id: str
    show: str
    title: str
    category: Category
    audioUrl: str = ""
    durationSec: int = Field(ge=0)
    publishedAt: str
    summary: str
    mentions: list[EpisodeMention] = Field(default_factory=list)


class BookRecord(BaseModel):
    title: str
    author: str
    isbn: str | None = None
    openLibraryKey: str | None = None
    episodeId: str
    show: str
    timestampSec: int = Field(ge=0)
    quote: str = ""
    endorsement: EndorsementLevel = "strong_rec"
    mentionCount: int = Field(default=1, ge=1)
    uniqueShowCount: int = Field(default=1, ge=1)
    mentionedByShows: list[str] = Field(default_factory=list)


class PlaceOrGearRecord(BaseModel):
    name: str
    type: PlaceType
    note: str
    episodeId: str
    timestampSec: int = Field(ge=0)
    show: str = ""
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lon: float | None = Field(default=None, ge=-180.0, le=180.0)
    endorsement: EndorsementLevel = "strong_rec"
    mentionCount: int = Field(default=1, ge=1)
    uniqueShowCount: int = Field(default=1, ge=1)
    mentionedByShows: list[str] = Field(default_factory=list)


class CityData(BaseModel):
    id: str
    name: str
    country: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    primaryCategory: Category
    tagline: str
    episodes: list[EpisodeRecord] = Field(default_factory=list)
    books: list[BookRecord] = Field(default_factory=list)
    placesAndGear: list[PlaceOrGearRecord] = Field(default_factory=list)


class CityFeatureProperties(BaseModel):
    id: str
    name: str
    country: str
    primaryCategory: Category
    categories: list[Category]
    episodeCount: int
    bookCount: int
    placeCount: int


class GeoJSONPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]  # (lon, lat)


class CityGeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONPoint
    properties: CityFeatureProperties


class CitiesGeoJSON(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[CityGeoJSONFeature]


class SearchIndexItem(BaseModel):
    kind: SearchKind
    title: str
    subtitle: str
    cityId: str
    tab: Literal["episodes", "books", "places"]
    episodeId: str | None = None
    timestampSec: int | None = None


# --- Raw LLM Extraction Schemas ---


class ExtractedCityRef(BaseModel):
    city: str
    country: str = ""
    tagline: str = ""


class ExtractedBook(BaseModel):
    title: str
    author: str = ""
    quote: str = ""
    timestampSec: int = Field(default=0, ge=0)
    endorsement: EndorsementLevel = "strong_rec"


class ExtractedPlaceOrGear(BaseModel):
    name: str
    type: PlaceType
    note: str = ""
    timestampSec: int = Field(default=0, ge=0)
    endorsement: EndorsementLevel = "strong_rec"


class EpisodeExtraction(BaseModel):
    isDestinationFocused: bool = True
    primaryCities: list[ExtractedCityRef] = Field(default_factory=list)
    summary: str = ""
    books: list[ExtractedBook] = Field(default_factory=list)
    placesAndGear: list[ExtractedPlaceOrGear] = Field(default_factory=list)


class CachedEpisodeTranscript(BaseModel):
    id: str
    guid: str
    showSlug: str
    showTitle: str
    category: Category
    title: str
    audioUrl: str
    durationSec: int = Field(ge=0)
    publishedAt: str
    description: str = ""
    transcriptSource: Literal["rss_tag", "faster_whisper"]
    segments: list[TranscriptSegment] = Field(default_factory=list)
