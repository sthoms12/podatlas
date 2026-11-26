"""GPU transcription fallback using faster-whisper (large-v3-turbo)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import httpx
import numpy as np

from .models import TranscriptSegment


def decode_audio_ffmpeg(audio_path: Path, sample_rate: int = 16000) -> np.ndarray:
    """Decode any audio file to 16kHz mono float32 PCM via ffmpeg (avoids PyAV version conflicts)."""
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads",
        "0",
        "-i",
        str(audio_path),
        "-f",
        "s16le",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(sample_rate),
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


class WhisperTranscriber:
    """Lazy-loading wrapper around faster-whisper for Google Colab L4/T4 GPUs."""

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def transcribe_audio_url(self, audio_url: str, timeout: float = 120.0) -> list[TranscriptSegment]:
        """Stream an episode MP3 to a temp file, transcribe, and clean up immediately."""
        model = self._ensure_model()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as tmp:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", audio_url, headers={"User-Agent": "PodAtlas/0.1"}) as resp:
                    resp.raise_for_status()
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        tmp.write(chunk)
            tmp.flush()
            return self.transcribe_file(Path(tmp.name), model=model)

    def transcribe_file(self, audio_path: Path, model=None) -> list[TranscriptSegment]:
        active_model = model or self._ensure_model()
        audio_array = decode_audio_ffmpeg(audio_path, sample_rate=16000)
        raw_segments, _info = active_model.transcribe(
            audio_array,
            language="en",
            vad_filter=True,
            beam_size=1,
        )
        return [
            TranscriptSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=seg.text.strip(),
            )
            for seg in raw_segments
            if seg.text.strip()
        ]
