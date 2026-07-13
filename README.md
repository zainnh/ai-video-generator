# AI Avatar TikTok Video Pipeline

A CLI-driven pipeline that turns a scene-by-scene script (or a single topic prompt) into a finished, captioned, vertical (1080×1920) talking-avatar MP4 ready for manual TikTok upload.

See [PRD.md](PRD.md) for full requirements, contracts, and test plan.

## Prerequisites

- **Python 3.10+**
- **ffmpeg** and **ffprobe** on your `PATH` ([install guide](https://ffmpeg.org/download.html))
- API keys:
  - [Anthropic](https://console.anthropic.com/) — script generation (prompt mode only)
  - [Muapi](https://muapi.ai/access-keys) — portrait + lip-sync generation

## Setup

```bash
cd /path/to/Automation

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys
```

On first TTS run, Coqui XTTS-v2 will download its model weights (~1.8 GB). This is a one-time download.

## Usage

### Web UI

Run the local web interface from the project root:

```bash
source .venv/bin/activate
python3 scripts/app.py
```

Open [http://localhost:5001](http://localhost:5001) in your browser (port 5001 avoids a conflict with macOS AirPlay Receiver on port 5000). Paste your scenes JSON, click **Generate**, and watch live progress. When finished, the results page plays your video and shows where it was saved under `output/<run_id>/final.mp4`.

Multiple jobs can run at the same time. Each submission gets its own job ID and background thread.

The CLI below still works independently if you prefer the terminal.

### Scene-list mode (recommended)

Write a `scenes.json` file (see `scripts/scenes.example.json`) and run:

```bash
python -m scripts.generate --scenes scripts/scenes.example.json
```

Each scene object requires:

| Field | Required | Description |
|---|---|---|
| `narration` | Yes | Spoken text for this scene |
| `avatar_image_prompt` | Yes | Portrait prompt for Muapi image generation |
| `target_seconds` | No | Pacing guide only — **not enforced** (see PRD Edge Case E7) |

**Avatar reuse:** scenes with the **exact same** `avatar_image_prompt` string share one portrait (case- and whitespace-sensitive). Near-identical prompts with different casing or trailing spaces will generate separate portraits.

### Prompt mode

Auto-generate script, caption, hashtags, and avatar prompt from a topic:

```bash
python -m scripts.generate --prompt "3 productivity hacks for remote workers"
```

Generated metadata is saved to `output/<run_id>/content.json`.

## Output

Each run creates a timestamped folder:

```
output/<run_id>/
  scene_00.wav          # per-scene TTS audio
  portrait_00.png       # unique portraits only (cached by prompt)
  scene_00.mp4          # per-scene talking video
  stitched.mp4          # concatenated scenes (multi-scene runs)
  captions.srt          # word-timed subtitles
  final.mp4             # captioned 1080×1920 output
```

## Configuration

| Variable | Required | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — |
| `MUAPI_API_KEY` | Yes | — |
| `MUAPI_BASE_URL` | No | `https://api.muapi.ai/api/v1` |
| `MUAPI_PORTRAIT_ENDPOINT` | No | `flux-schnell-image` |
| `MUAPI_LIPSYNC_ENDPOINT` | No | `kling-v2-avatar-standard` |

Verify Muapi endpoint names against the [Muapi docs](https://muapi.ai/docs/api-reference) before your first real run (see PRD Milestone M2).

## Running tests

```bash
pytest tests/ -v
```

Unit tests mock external APIs. Integration and end-to-end tests require live API keys and are skipped by default.

## Pipeline stages

1. **Validate** input (scenes JSON or prompt)
2. **TTS** — local Coqui XTTS-v2 (free)
3. **Portrait** — Muapi text-to-image (cached by exact prompt string)
4. **Talking video** — Muapi lip-sync model
5. **Concat** — ffmpeg stitches scene clips (skipped for single-scene runs)
6. **Postprocess** — faster-whisper captions + ffmpeg burn-in + 9:16 crop

## Known limitations

- No auto-posting to TikTok
- `target_seconds` is advisory; actual duration depends on TTS output
- No automatic retry on Muapi failures
- Each run gets a fresh `run_id`; resume-from-failure across runs is not supported
- Portrait caching uses exact string matching, not semantic similarity
