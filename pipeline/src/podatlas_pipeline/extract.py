"""Structured JSON extraction using Qwen2.5-7B-Instruct / Qwen3-8B (vLLM or OpenAI-compatible API)."""

from __future__ import annotations

import json
import re
from typing import Iterable

import httpx

from .models import EpisodeExtraction, TranscriptSegment

SYSTEM_PROMPT = """You are PodAtlas's precision editorial extractor.
Given a timestamped podcast episode transcript (formatted as [MM:SS] text), extract:
1. primaryCities: ONLY the 1-2 primary destination cities this episode is ABOUT.
   - STRICTLY IGNORE where the host lives, where the podcast is recorded, or sponsor headquarters.
   - If the episode is NOT meaningfully about a specific city, set isDestinationFocused=false and primaryCities=[].
2. summary: A crisp 1-2 sentence summary focused on what a traveler or history/food fan learns about the city.
3. books: Books explicitly recommended or discussed, with author, short host quote/reason, timestampSec, and endorsement ("strong_rec" if explicitly praised/recommended, "casual_mention" if mentioned in passing).
4. placesAndGear: Specific restaurants, cafes, temples, museums, neighborhoods (type "Landmark" or "Food"), or travel gear items (type "Gear") mentioned, with a helpful note, timestampSec, and endorsement ("strong_rec" or "casual_mention").

Return ONLY valid JSON matching this schema:
{
  "isDestinationFocused": true,
  "primaryCities": [{"city": "Kyoto", "country": "Japan", "tagline": "Short city tagline"}],
  "summary": "1-2 sentence episode summary.",
  "books": [{"title": "Book Title", "author": "Author Name", "quote": "Why mentioned", "timestampSec": 840, "endorsement": "strong_rec"}],
  "placesAndGear": [{"name": "Place or Gear", "type": "Landmark|Food|Gear", "note": "Why notable", "timestampSec": 1120, "endorsement": "strong_rec"}]
}"""


def format_transcript_for_llm(
    segments: Iterable[TranscriptSegment],
    max_chars: int = 36000,
) -> str:
    """Render timestamped segments into compact [MM:SS] lines capped for 7B/8B context."""
    lines: list[str] = []
    total = 0
    for seg in segments:
        sec = int(seg.start)
        mm, ss = divmod(sec, 60)
        line = f"[{mm:02d}:{ss:02d}] {seg.text}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def parse_llm_json(raw_text: str) -> EpisodeExtraction:
    """Parse raw LLM output (stripping markdown fences if present) into EpisodeExtraction."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    payload = json.loads(cleaned)
    return EpisodeExtraction.model_validate(payload)


class LLMExtractor:
    """OpenAI-compatible client targeting a local vLLM server (Colab) or hosted endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def extract_episode(
        self,
        show_title: str,
        episode_title: str,
        segments: list[TranscriptSegment],
    ) -> EpisodeExtraction:
        transcript_text = format_transcript_for_llm(segments)
        user_prompt = (
            f"Show: {show_title}\n"
            f"Episode: {episode_title}\n\n"
            f"Transcript:\n{transcript_text}"
        )

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        return parse_llm_json(content)
