"""Configuration settings for the PodAtlas pipeline."""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PODATLAS_", extra="ignore")

    podcast_index_key: str = ""
    podcast_index_secret: str = ""
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"

    shows_csv: Path = Path("shows.csv")
    cache_dir: Path = Path("cache")
    output_dir: Path = Path("../web/public")
    site_base_url: str = "https://podatlas.app"
    min_duration_sec: int = 600
