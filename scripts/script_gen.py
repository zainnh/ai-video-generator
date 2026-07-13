"""Generate TikTok script content via Claude API."""

from __future__ import annotations

import json
import re

import anthropic

from scripts.config import ANTHROPIC_API_KEY

MODEL = "claude-sonnet-4-6"
REQUIRED_KEYS = ("script", "caption", "hashtags", "avatar_image_prompt")


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    return json.loads(text)


def _validate_content(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    for key in REQUIRED_KEYS:
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

    script = data["script"]
    caption = data["caption"]
    hashtags = data["hashtags"]
    avatar_image_prompt = data["avatar_image_prompt"]

    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be a non-empty string")
    if not isinstance(caption, str) or not caption.strip():
        raise ValueError("caption must be a non-empty string")
    if len(caption.strip()) > 150:
        raise ValueError("caption must be ≤ 150 characters")
    if not isinstance(hashtags, list) or len(hashtags) < 1:
        raise ValueError("hashtags must be a non-empty list")
    for tag in hashtags:
        if not isinstance(tag, str) or not tag.startswith("#"):
            raise ValueError(f"Each hashtag must start with '#': {tag!r}")
    if not isinstance(avatar_image_prompt, str) or not avatar_image_prompt.strip():
        raise ValueError("avatar_image_prompt must be a non-empty string")

    return {
        "script": script.strip(),
        "caption": caption.strip(),
        "hashtags": [t.strip() for t in hashtags],
        "avatar_image_prompt": avatar_image_prompt.strip(),
    }


def generate_content(niche_prompt: str) -> dict:
    """Generate script, caption, hashtags, and avatar prompt from a niche topic."""
    if not isinstance(niche_prompt, str) or not niche_prompt.strip():
        raise ValueError("niche_prompt must be a non-empty string")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system_prompt = """You write short-form TikTok scripts for AI avatar videos.
Respond with ONLY valid JSON (no markdown fences) containing exactly these keys:
- script: spoken narration (2-4 sentences, conversational)
- caption: TikTok post caption (≤150 chars)
- hashtags: array of 3-5 hashtag strings, each starting with #
- avatar_image_prompt: detailed portrait prompt for a vertical 9:16 talking-head avatar"""

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": niche_prompt.strip()}],
    )

    raw_text = message.content[0].text

    try:
        parsed = _extract_json(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude response is not valid JSON: {exc}. Raw response: {raw_text}"
        ) from exc

    return _validate_content(parsed)
