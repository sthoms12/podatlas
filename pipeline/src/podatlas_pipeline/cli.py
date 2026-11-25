"""Command-line interface for running and validating the PodAtlas pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import PipelineSettings
from .export_static import export_static_site_data, load_existing_cities
from .extract import LLMExtractor
from .geocode import GeoNamesIndex
from .models import BookRecord, CityData, EpisodeMention, EpisodeRecord, PlaceOrGearRecord
from .podcast_index import fetch_rss_episodes, fetch_transcript_if_available, load_shows_csv
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


def run_pipeline(max_episodes: int | None = None, verify_books: bool = True) -> dict[str, int]:
    settings = PipelineSettings()
    shows = load_shows_csv(settings.shows_csv)
    checkpoint_path = settings.cache_dir / "last_processed.json"
    processed = _load_checkpoint(checkpoint_path)

    geonames = GeoNamesIndex.download_and_load(settings.cache_dir)
    transcriber = WhisperTranscriber(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    extractor = LLMExtractor(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    book_verifier = OpenLibraryVerifier(cache_path=settings.cache_dir / "open_library_cache.json")
    cities_by_id = load_existing_cities(settings.output_dir)

    episodes_run = 0
    for show in shows:
        raw_episodes = fetch_rss_episodes(show)
        for raw_ep in raw_episodes:
            if raw_ep.guid in processed:
                continue
            if max_episodes is not None and episodes_run >= max_episodes:
                break

            segments = fetch_transcript_if_available(raw_ep.transcript_url)
            if not segments:
                segments = transcriber.transcribe_audio_url(raw_ep.audio_url)

            extraction = extractor.extract_episode(show.title, raw_ep.title, segments)
            processed.add(raw_ep.guid)
            episodes_run += 1

            if not extraction.isDestinationFocused or not extraction.primaryCities:
                continue

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
                        primaryCategory=raw_ep.category,
                        tagline=city_ref.tagline or f"Curated podcast episodes and recommendations for {geo.name}.",
                    )
                    cities_by_id[geo.id] = city

                mentions: list[EpisodeMention] = []
                for b in extraction.books:
                    verified = book_verifier.verify(b.title, b.author) if verify_books else None
                    if verify_books and not verified:
                        continue
                    final_title = verified.title if verified else b.title
                    final_author = verified.author if verified else (b.author or "Unknown Author")
                    city.books.append(
                        BookRecord(
                            title=final_title,
                            author=final_author,
                            isbn=verified.isbn if verified else None,
                            openLibraryKey=verified.open_library_key if verified else None,
                            episodeId=raw_ep.id,
                            show=raw_ep.show_title,
                            timestampSec=b.timestampSec,
                            quote=b.quote,
                        )
                    )
                    mentions.append(EpisodeMention(sec=b.timestampSec, type="book", label=final_title))

                for pl in extraction.placesAndGear:
                    city.placesAndGear.append(
                        PlaceOrGearRecord(
                            name=pl.name,
                            type=pl.type,
                            note=pl.note,
                            episodeId=raw_ep.id,
                            timestampSec=pl.timestampSec,
                        )
                    )
                    m_type = "gear" if pl.type == "Gear" else "place"
                    mentions.append(EpisodeMention(sec=pl.timestampSec, type=m_type, label=pl.name))

                city.episodes.append(
                    EpisodeRecord(
                        id=raw_ep.id,
                        show=raw_ep.show_title,
                        title=raw_ep.title,
                        category=raw_ep.category,
                        audioUrl=raw_ep.audio_url,
                        durationSec=raw_ep.duration_sec,
                        publishedAt=raw_ep.published_at,
                        summary=extraction.summary or raw_ep.description[:220],
                        mentions=mentions,
                    )
                )

        if max_episodes is not None and episodes_run >= max_episodes:
            break

    _save_checkpoint(checkpoint_path, processed)
    return export_static_site_data(
        list(cities_by_id.values()),
        output_dir=settings.output_dir,
        site_base_url=settings.site_base_url,
    )


def validate_output(output_dir: Path) -> dict[str, int]:
    cities = load_existing_cities(output_dir)
    return {
        "cities": len(cities),
        "episodes": sum(len(c.episodes) for c in cities.values()),
        "books": sum(len(c.books) for c in cities.values()),
        "placesAndGear": sum(len(c.placesAndGear) for c in cities.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PodAtlas offline extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Ingest new episodes and export static JSONs")
    run_cmd.add_argument("--max-episodes", type=int, default=None, help="Limit episodes processed in this batch")
    run_cmd.add_argument("--skip-book-verify", action="store_true", help="Skip Open Library API verification")

    val_cmd = sub.add_parser("validate", help="Validate exported static JSON files")
    val_cmd.add_argument("--output-dir", type=Path, default=Path("../web/public"))

    args = parser.parse_args()
    if args.command == "run":
        stats = run_pipeline(max_episodes=args.max_episodes, verify_books=not args.skip_book_verify)
        print(json.dumps(stats, indent=2))
    elif args.command == "validate":
        stats = validate_output(args.output_dir)
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
