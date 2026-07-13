"""Muapi.ai REST client for portrait and talking-video generation."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import requests

from scripts.config import MUAPI_API_KEY, MUAPI_BASE_URL

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_TIMEOUT_S = 600.0
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _headers() -> dict[str, str]:
    return {
        "x-api-key": MUAPI_API_KEY,
        "Content-Type": "application/json",
    }


def _media_duration_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def submit_job(endpoint: str, payload: dict) -> str:
    """Submit a generation job and return request_id."""
    url = f"{MUAPI_BASE_URL}/{endpoint.lstrip('/')}"
    response = requests.post(url, json=payload, headers=_headers(), timeout=60)
    response.raise_for_status()
    data = response.json()
    request_id = data.get("request_id")
    if not request_id:
        raise RuntimeError(f"Muapi submit response missing request_id: {data}")
    return request_id


def poll_result(
    request_id: str,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Poll until job completes, fails, or times out."""
    url = f"{MUAPI_BASE_URL}/predictions/{request_id}/result"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        response = requests.get(url, headers=_headers(), timeout=60)
        response.raise_for_status()
        data = response.json()
        status = data.get("status", "")

        if status == "failed":
            raise RuntimeError(f"Muapi job failed: {data}")
        if status == "cancelled":
            raise RuntimeError(f"Muapi job cancelled: {data}")
        if status == "completed":
            return data

        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"Muapi job timed out after {timeout_s}s (request_id={request_id})"
    )


def _download_file(url: str, out_path: Path) -> Path:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)
    if out_path.stat().st_size == 0:
        raise RuntimeError(f"Downloaded file is empty: {out_path}")
    return out_path


def _first_output_url(result: dict) -> str:
    outputs = result.get("outputs") or []
    if not outputs:
        raise RuntimeError(f"Muapi completed job has no outputs: {result}")
    return outputs[0]


def upload_file(file_path: Path) -> str:
    """Upload a local file to Muapi and return the hosted URL."""
    url = f"{MUAPI_BASE_URL}/upload_file"
    with file_path.open("rb") as file_handle:
        response = requests.post(
            url,
            headers={"x-api-key": MUAPI_API_KEY},
            files={"file": (file_path.name, file_handle)},
            timeout=120,
        )
    response.raise_for_status()
    data = response.json()
    hosted_url = data.get("url") or data.get("file_url") or data.get("hosted_url")
    if not hosted_url:
        raise RuntimeError(f"Muapi upload response missing URL: {data}")
    return hosted_url


def generate_portrait(
    prompt: str,
    out_path: Path,
    model_endpoint: str,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Path:
    """Generate a portrait image from a text prompt."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    out_path = Path(out_path)
    payload = {
        "prompt": prompt.strip(),
        "aspect_ratio": "9:16",
    }
    request_id = submit_job(model_endpoint, payload)
    result = poll_result(request_id, poll_interval_s, timeout_s)
    output_url = _first_output_url(result)
    return _download_file(output_url, out_path)


def generate_talking_video(
    portrait_path: Path,
    audio_path: Path,
    out_path: Path,
    model_endpoint: str,
    resolution: str = "720p",
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Path:
    """Generate a lip-synced talking video from a portrait and audio file."""
    portrait_path = Path(portrait_path)
    audio_path = Path(audio_path)
    out_path = Path(out_path)

    if not portrait_path.exists():
        raise FileNotFoundError(f"Portrait not found: {portrait_path}")
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    image_url = upload_file(portrait_path)
    audio_url = upload_file(audio_path)
    audio_duration = _media_duration_seconds(audio_path)

    payload = {
        "image_url": image_url,
        "audio_url": audio_url,
        "resolution": resolution,
    }
    request_id = submit_job(model_endpoint, payload)
    result = poll_result(request_id, poll_interval_s, timeout_s)
    output_url = _first_output_url(result)
    _download_file(output_url, out_path)

    video_duration = _media_duration_seconds(out_path)
    if abs(video_duration - audio_duration) > 1.5:
        raise RuntimeError(
            f"Talking video duration {video_duration:.2f}s differs from audio "
            f"{audio_duration:.2f}s by more than 1.5s"
        )

    return out_path
