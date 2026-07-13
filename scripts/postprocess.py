"""Captioning and vertical video formatting with faster-whisper and ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path

_whisper_model = None

# Subtitle styling tuned for TikTok legibility
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,Outline=2,Shadow=1,Alignment=2,MarginV=80"
)


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["ffmpeg", *args],
            output=result.stdout,
            stderr=result.stderr,
        )


def _format_srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def transcribe_words(audio_path: Path) -> list[dict]:
    """Transcribe audio and return word-level timestamps."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    model = _get_whisper_model()
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True)

    words: list[dict] = []
    for segment in segments:
        if not segment.words:
            continue
        for word in segment.words:
            text = (word.word or "").strip()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "start": float(word.start),
                    "end": float(word.end),
                }
            )

    words.sort(key=lambda w: w["start"])
    return words


def build_srt(words: list[dict], out_srt: Path, chunk_size: int = 4) -> Path:
    """Build an SRT subtitle file from word timestamps."""
    out_srt = Path(out_srt)
    out_srt.parent.mkdir(parents=True, exist_ok=True)

    if not words:
        out_srt.write_text("", encoding="utf-8")
        return out_srt

    lines: list[str] = []
    index = 1
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        start = chunk[0]["start"]
        end = chunk[-1]["end"]
        text = " ".join(w["text"] for w in chunk)
        lines.append(str(index))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
        index += 1

    out_srt.write_text("\n".join(lines), encoding="utf-8")
    return out_srt


def render_final(video_path: Path, srt_path: Path, out_path: Path) -> Path:
    """Burn captions and crop/scale video to 1080x1920 (9:16)."""
    video_path = Path(video_path)
    srt_path = Path(srt_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    escaped_srt = str(srt_path).replace(":", r"\:").replace("'", r"\'")
    vf = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,subtitles='{escaped_srt}':force_style='{SUBTITLE_STYLE}'"
    )

    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )

    return out_path


def get_video_resolution(video_path: Path) -> tuple[int, int]:
    """Return (width, height) of a video file via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)
