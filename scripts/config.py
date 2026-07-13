"""Load and validate environment variables for the avatar video pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_REQUIRED_KEYS = ("ANTHROPIC_API_KEY", "MUAPI_API_KEY")


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value or not value.strip():
        raise KeyError(key)
    return value.strip()


ANTHROPIC_API_KEY: str = _require_env("ANTHROPIC_API_KEY")
MUAPI_API_KEY: str = _require_env("MUAPI_API_KEY")
MUAPI_BASE_URL: str = os.getenv("MUAPI_BASE_URL", "https://api.muapi.ai/api/v1").rstrip("/")
MUAPI_PORTRAIT_ENDPOINT: str = os.getenv("MUAPI_PORTRAIT_ENDPOINT", "flux-schnell-image")
MUAPI_LIPSYNC_ENDPOINT: str = os.getenv("MUAPI_LIPSYNC_ENDPOINT", "kling-v2-avatar-standard")
WEB_PORT: int = int(os.getenv("WEB_PORT", "5001"))

OUTPUT_DIR: Path = _PROJECT_ROOT / "output"
LOG_DIR: Path = _PROJECT_ROOT / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
