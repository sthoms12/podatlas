"""Command-line interface for running PodAtlas's two-pass extraction pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import PipelineSettings
from .export_static import export_static_site_data, load_existing_cities
from .extract import LLMExtractor
from .geocode import GeoNamesIndex, normalize_key
from .models import (
    BookRecord,
    CachedEpisodeTranscript,
    CityData,
    EpisodeExtraction,
    EpisodeMention,
    EpisodeRecord,
    PlaceOrGearRecord,
)
from .podcast_index import (
    check_show_feed,
    fetch_rss_episodes,
    fetch_transcript_if_available,
    is_skippable_episode,
    load_shows_csv,
)
from .transcribe import WhisperTranscriber
from .verify_books import OpenLibraryVerifier


def _load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("processed_guids", []))


def _save_checkpoint(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"processed_guids": sorted(processed)}, indent=2) + "\n",
        encoding="utf-8",
    )


def transcript_cache_path(cache_dir: Path, episode_id: str) -> Path:
    return cache_dir / "transcripts" / f"{episode_id}.json"


def save_cached_transcript(cache_dir: Path, item: CachedEpisodeTranscript) -> Path:
    path = transcript_cache_path(cache_dir, item.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_cached_transcripts(cache_dir: Path) -> list[CachedEpisodeTranscript]:
    transcripts_dir = cache_dir / "transcripts"
    if not transcripts_dir.exists():
        return []
    items: list[CachedEpisodeTranscript] = []
    for path in sorted(transcripts_dir.glob("*.json")):
        items.append(CachedEpisodeTranscript.model_validate_json(path.read_text(encoding="utf-8")))
    return items


def merge_extraction_into_cities(
    cities_by_id: dict[str, CityData],
    cached_ep: CachedEpisodeTranscript,
    extraction: EpisodeExtraction,
    geonames: GeoNamesIndex,
    book_verifier: OpenLibraryVerifier | None = None,
) -> int:
    """Idempotently merge one episode's extraction into cities_by_id. Returns cities updated."""
    if not extraction.isDestinationFocused or not extraction.primaryCities:
        return 0

    updated = 0
    for city_ref in extraction.primaryCities:
        geo = geonames.lookup(city_ref.city, city_ref.country)
        if not geo:
            continue

        city = cities_by_id.get(geo.id)
        if not city:
            city = CityData(
                id=geo.id,
                name=geo.name,
                country=geo.country,
                lat=geo.lat,
                lon=geo.lon,
                primaryCategory=cached_ep.category,
                tagline=city_ref.tagline
                or f"Curated podcast episodes and recommendations for {geo.name}.",
            )
            cities_by_id[geo.id] = city

        # Idempotent dedupe: skip if this episode ID is already indexed for this city
        if any(existing.id == cached_ep.id for existing in city.episodes):
            continue

        existing_book_keys = {
            (normalize_key(b.title), b.episodeId) for b in city.books
        }
        existing_place_keys = {
            (normalize_key(p.name), p.episodeId) for p in city.placesAndGear
        }

        mentions: list[EpisodeMention] = []
        for b in extraction.books:
            verified = book_verifier.verify(b.title, b.author) if book_verifier else None
            if book_verifier and not verified:
                continue
            final_title = verified.title if verified else b.title
            final_author = verified.author if verified else (b.author or "Unknown Author")
            book_key = (normalize_key(final_title), cached_ep.id)
            if book_key in existing_book_keys:
                continue
            existing_book_keys.add(book_key)
            city.books.append(
                BookRecord(
                    title=final_title,
                    author=final_author,
                    isbn=verified.isbn if verified else None,
                    openLibraryKey=verified.open_library_key if verified else None,
                    episodeId=cached_ep.id,
                    show=cached_ep.showTitle,
                    timestampSec=b.timestampSec,
                    quote=b.quote,
                )
            )
            mentions.append(EpisodeMention(sec=b.timestampSec, type="book", label=final_title))

        for pl in extraction.placesAndGear:
            place_key = (normalize_key(pl.name), cached_ep.id)
            if place_key in existing_place_keys:
                continue
            existing_place_keys.add(place_key)
            city.placesAndGear.append(
                PlaceOrGearRecord(
                    name=pl.name,
                    type=pl.type,
                    note=pl.note,
                    episodeId=cached_ep.id,
                    timestampSec=pl.timestampSec,
                )
            )
            m_type = "gear" if pl.type == "Gear" else "place"
            mentions.append(EpisodeMention(sec=pl.timestampSec, type=m_type, label=pl.name))

        city.episodes.append(
            EpisodeRecord(
                id=cached_ep.id,
                show=cached_ep.showTitle,
                title=cached_ep.title,
                category=cached_ep.category,
                audioUrl=cached_ep.audioUrl,
                durationSec=cached_ep.durationSec,
                publishedAt=cached_ep.publishedAt,
                summary=extraction.summary or cached_ep.description[:220],
                mentions=mentions,
            )
        )
        updated += 1

    return updated


def run_transcribe_pass(max_episodes: int | None = None) -> dict[str, int]:
    """Pass 1: Fetch RSS feeds, skip trailers, check <podcast:transcript>, or run faster-whisper."""
    settings = PipelineSettings()
    shows = load_shows_csv(settings.shows_csv)
    checkpoint_path = settings.cache_dir / "last_processed.json"
    processed = _load_checkpoint(checkpoint_path)

    transcriber = WhisperTranscriber(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )

    stats = {
        "cachedFromRss": 0,
        "transcribedWithWhisper": 0,
        "skippedAlreadyCached": 0,
        "skippedFiltered": 0,
        "feedErrors": 0,
    }
    newly_cached = 0

    for show in shows:
        try:
            raw_episodes = fetch_rss_episodes(show)
        except Exception as exc:
            print(f"[warn] Feed failed for {show.slug}: {exc}")
            stats["feedErrors"] += 1
            continue

        for raw_ep in raw_episodes:
            if max_episodes is not None and newly_cached >= max_episodes:
                break

            if raw_ep.guid in processed or transcript_cache_path(settings.cache_dir, raw_ep.id).exists():
                stats["skippedAlreadyCached"] += 1
                continue

            skip, _reason = is_skippable_episode(raw_ep, min_duration_sec=settings.min_duration_sec)
            if skip:
                stats["skippedFiltered"] += 1
                processed.add(raw_ep.guid)
                continue

            segments = fetch_transcript_if_available(raw_ep.transcript_url)
            source: str = "rss_tag"
            if not segments:
                segments = transcriber.transcribe_audio_url(raw_ep.audio_url)
                source = "faster_whisper"
                stats["transcribedWithWhisper"] += 1
            else:
                stats["cachedFromRss"] += 1

            save_cached_transcript(
                settings.cache_dir,
                CachedEpisodeTranscript(
                    id=raw_ep.id,
                    guid=raw_ep.guid,
                    showSlug=raw_ep.show_slug,
                    showTitle=raw_ep.show_title,
                    category=raw_ep.category,
                    title=raw_ep.title,
                    audioUrl=raw_ep.audio_url,
                    durationSec=raw_ep.duration_sec,
                    publishedAt=raw_ep.published_at,
                    description=raw_ep.description,
                    transcriptSource=source,  # type: ignore[arg-type]
                    segments=segments,
                ),
            )
            newly_cached += 1

        if max_episodes is not None and newly_cached >= max_episodes:
            break

    _save_checkpoint(checkpoint_path, processed)
    return stats


def run_extract_pass(
    max_episodes: int | None = None,
    verify_books: bool = True,
    reprocess: bool = False,
) -> dict[str, int]:
    """Pass 2: Run vLLM JSON extraction over cached transcripts, geocode, verify books, and export."""
    settings = PipelineSettings()
    checkpoint_path = settings.cache_dir / "last_processed.json"
    processed = _load_checkpoint(checkpoint_path)

    geonames = GeoNamesIndex.download_and_load(settings.cache_dir)
    extractor = LLMExtractor(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    book_verifier = (
        OpenLibraryVerifier(cache_path=settings.cache_dir / "open_library_cache.json")
        if verify_books
        else None
    )
    cities_by_id = load_existing_cities(settings.output_dir)
    cached_items = load_cached_transcripts(settings.cache_dir)

    extracted_count = 0
    for cached_ep in cached_items:
        if not reprocess and cached_ep.guid in processed:
            continue
        if max_episodes is not None and extracted_count >= max_episodes:
            break

        extraction = extractor.extract_episode(
            cached_ep.showTitle,
            cached_ep.title,
            cached_ep.segments,
        )
        merge_extraction_into_cities(
            cities_by_id=cities_by_id,
            cached_ep=cached_ep,
            extraction=extraction,
            geonames=geonames,
            book_verifier=book_verifier,
        )
        processed.add(cached_ep.guid)
        extracted_count += 1
        # Save checkpoint incrementally so Colab disconnects never lose progress
        _save_checkpoint(checkpoint_path, processed)

    stats = export_static_site_data(
        list(cities_by_id.values()),
        output_dir=settings.output_dir,
        site_base_url=settings.site_base_url,
    )
    stats["episodesExtractedThisRun"] = extracted_count
    return stats


def run_check_feeds() -> list[dict[str, object]]:
    settings = PipelineSettings()
    shows = load_shows_csv(settings.shows_csv)
    return [check_show_feed(show) for show in shows]


def validate_output(output_dir: Path) -> dict[str, int]:
    cities = load_existing_cities(output_dir)
    return {
        "cities": len(cities),
        "episodes": sum(len(c.episodes) for c in cities.values()),
        "books": sum(len(c.books) for c in cities.values()),
        "placesAndGear": sum(len(c.placesAndGear) for c in cities.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PodAtlas two-pass offline extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("transcribe", help="Pass 1: Fetch RSS / <podcast:transcript> / faster-whisper into cache")
    p1.add_argument("--max-episodes", type=int, default=None, help="Max new episodes to cache this run")

    p2 = sub.add_parser("extract", help="Pass 2: Run vLLM extraction over cached transcripts and export static JSON")
    p2.add_argument("--max-episodes", type=int, default=None, help="Max cached episodes to extract this run")
    p2.add_argument("--skip-book-verify", action="store_true", help="Skip Open Library API verification")
    p2.add_argument("--reprocess", action="store_true", help="Re-run extraction over already processed transcripts")

    run_cmd = sub.add_parser("run", help="Run Pass 1 (transcribe) followed by Pass 2 (extract)")
    run_cmd.add_argument("--max-episodes", type=int, default=None)
    run_cmd.add_argument("--skip-book-verify", action="store_true")

    sub.add_parser("check-feeds", help="CPU-only check of all RSS feeds in shows.csv")

    val_cmd = sub.add_parser("validate", help="Validate exported static JSON files")
    val_cmd.add_argument("--output-dir", type=Path, default=Path("../web/public"))

    args = parser.parse_args()
    if args.command == "transcribe":
        print(json.dumps(run_transcribe_pass(max_episodes=args.max_episodes), indent=2))
    elif args.command == "extract":
        print(
            json.dumps(
                run_extract_pass(
                    max_episodes=args.max_episodes,
                    verify_books=not args.skip_book_verify,
                    reprocess=args.reprocess,
                ),
                indent=2,
            )
        )
    elif args.command == "run":
        t_stats = run_transcribe_pass(max_episodes=args.max_episodes)
        e_stats = run_extract_pass(
            max_episodes=args.max_episodes,
            verify_books=not args.skip_book_verify,
        )
        print(json.dumps({"transcribe": t_stats, "extract": e_stats}, indent=2))
    elif args.command == "check-feeds":
        results = run_check_feeds()
        ok_count = sum(1 for r in results if r["ok"])
        free_tx = sum(int(r["withFreeTranscript"]) for r in results)  # type: ignore[arg-type]
        print(json.dumps({"feedsOk": ok_count, "feedsTotal": len(results), "freeTranscriptEpisodes": free_tx, "details": results}, indent=2))
    elif args.command == "validate":
        print(json.dumps(validate_output(args.output_dir), indent=2))


if __name__ == "__main__":
    main()
