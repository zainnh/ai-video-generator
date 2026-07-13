"""Local text-to-speech using Coqui XTTS-v2."""

from __future__ import annotations

import wave
from pathlib import Path

_tts_model = None


def _get_model():
    global _tts_model
    if _tts_model is None:
        from TTS.api import TTS

        _tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
    return _tts_model


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
        if rate == 0:
            return 0.0
        return frames / float(rate)


def synthesize(
    text: str,
    out_path: Path,
    speaker_wav: str | None = None,
    language: str = "en",
) -> Path:
    """Synthesize speech to a WAV file using XTTS-v2."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")

    out_path = Path(out_path)
    if not out_path.parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {out_path.parent}")

    model = _get_model()
    kwargs: dict = {
        "text": text.strip(),
        "file_path": str(out_path),
        "language": language,
    }
    if speaker_wav:
        kwargs["speaker_wav"] = speaker_wav
    else:
        kwargs["speaker"] = "Claribel Dervla"

    model.tts_to_file(**kwargs)

    if not out_path.exists():
        raise RuntimeError(f"TTS failed to create output file: {out_path}")

    duration = _wav_duration_seconds(out_path)
    if duration <= 0:
        raise RuntimeError(f"TTS produced zero-duration audio: {out_path}")

    return out_path
