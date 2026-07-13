# AI Avatar Video Generator

I wanted to make faceless-style AI avatar videos for TikTok without spending an hour bouncing between five different tools every time - write a script somewhere, generate a voice somewhere else, generate an avatar video somewhere else, add captions somewhere else, resize it, and finally upload. So I built this to collapse all of that into: write my scenes, hit generate, get one finished video.

It's not an app, it's not a product, it's a script (with a small local web UI on top) that I run on my own machine. You paste in your script, it goes and generates everything, and you get back a captioned, vertical, ready-to-post `.mp4`.

## How you actually use it

You write your video as a list of scenes - each one is a line of narration plus a description of who's saying it. Something like:

```json
[
  {
    "narration": "Did you know octopuses have three hearts and blue blood?",
    "avatar_image_prompt": "friendly female marine biologist, wetsuit, aquarium background"
  },
  {
    "narration": "And when they swim, the main heart actually stops beating.",
    "avatar_image_prompt": "dramatic close-up host, dark background, intense expression"
  }
]
```

Same `avatar_image_prompt` on every scene keeps one consistent face throughout the video. Different prompts per scene means the avatar changes. Either works - it's just text.

Paste that into the web UI, hit Generate, and it goes off and does everything: writes the voice audio, generates the avatar portrait, animates it speaking, stitches all the scenes together, burns in captions, crops to 9:16. You get a finished video back. Post it wherever you want - the tool doesn't touch your TikTok account, that part's still on you.

If you don't want to write a script yourself, you can also just give it a one-line topic and let an LLM write the whole thing for you.

## Why I built it this way

I looked at whether I could just build my own AI models for this and - no, not realistically, not without a research team and a pile of GPUs. So instead this leans entirely on things that already exist and are good at their one job:

- **Coqui XTTS** does the voice. It's open source, runs locally on your own machine, completely free, and comes with 50+ built-in voices (plus voice cloning, if you ever want to go there).
- **Muapi.ai** does the actual avatar generation. It's an API that gives access to a huge catalog of image and video models - I'm using Flux for the portrait and InfiniteTalk for the lip-sync - all through one API key, pay-per-generation, no subscription. This is the only piece that costs real money, and it's cheap, usually a few cents per generation.
- **faster-whisper** handles captions. Also local, also free, and it gives word-level timing so the captions actually track what's being said instead of just sitting there as one static block.
- **ffmpeg** stitches the per-scene clips together and crops everything to vertical at the end.
- **Grok (via xAI's API)** writes the script when I don't feel like writing it myself, and picks a voice that fits each avatar when I don't specify one.

So really, this repo isn't "an AI" - it's the glue code that makes five separate things talk to each other in the right order so I don't have to do it by hand every time.

## What it deliberately doesn't do

- **It doesn't post anywhere for you.** You watch the final video, you decide if it's good, you upload it yourself. I didn't want a tool that publishes on its own without me looking at it first.
- **It doesn't force exact scene timing.** The narration you write drives how long a scene ends up being - there's no trimming or stretching audio to hit a number.
- **It doesn't remember progress if something fails halfway.** If a run breaks partway through, you just re-run it. I didn't build resume logic because it wasn't worth the complexity for how I actually use this.

None of that felt necessary for what I actually wanted, which was: write a script, get a video, upload it myself.

## Project layout

```
.
├── scripts/
│   ├── app.py              - the local web UI (Flask). Run this, open localhost, paste scenes, generate.
│   ├── generate.py          - the actual pipeline. Takes scenes (or a prompt), runs everything in
│   │                          parallel (voices, portraits, avatar videos), stitches it all together.
│   ├── script_gen.py        - talks to Grok: writes a full script from a topic, and picks a voice
│   │                          to match an avatar when you don't specify one yourself.
│   ├── tts_xtts.py          - wraps Coqui XTTS. Turns narration text into a voice audio file.
│   ├── avatar_muapi.py      - talks to Muapi's API. Generates the portrait image, then animates it
│   │                          speaking with the audio (lip-sync).
│   ├── postprocess.py       - runs faster-whisper for word timing, then ffmpeg to burn in captions
│   │                          and crop everything to 9:16.
│   ├── config.py            - loads API keys and settings from .env
│   ├── templates/           - the two HTML pages behind the web UI (input form, results page)
│   └── scenes.example.json  - a working example you can run immediately
├── requirements.txt
├── .env.example
└── output/                  - every run lands here as output/<run_id>/final.mp4, with all the
                                intermediate files kept alongside it (portraits, raw audio, etc.)
```

## Setting it up

You'll need Python 3.11+, ffmpeg installed, a Muapi.ai account with some credit loaded, and an xAI (Grok) API key if you want the auto-script/auto-voice features.

```bash
git clone <your-repo-url>
cd <repo-folder>
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

macOS: `brew install ffmpeg`. Linux: `sudo apt install ffmpeg`.

```bash
cp .env.example .env
```

Drop your `MUAPI_API_KEY` and `XAI_API_KEY` into `.env`.

Pre-download the voice model once, before you actually need it (it's about 2GB):

```bash
python3 -c "from TTS.api import TTS; TTS('tts_models/multilingual/multi-dataset/xtts_v2')"
```

Then run it:

```bash
python3 scripts/app.py
```

Open `http://localhost:5000`, paste your scenes, hit Generate. Or skip the UI entirely and run it from the terminal:

```bash
python3 scripts/generate.py --scenes scripts/scenes.example.json
```

Either way, your finished video shows up at `output/<run_id>/final.mp4`.

## License

MIT - do whatever you want with it.
