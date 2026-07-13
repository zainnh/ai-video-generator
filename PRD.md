# PRD: AI Avatar Video Generation Pipeline for TikTok

**Owner:** Zain Haider
**Status:** Draft — v2 (detailed, implementation-grade)
**Last updated:** July 12, 2026
**Purpose of this revision:** Previous version defined *what* to build. This version
defines *what "done and correct" means* for every requirement — exact contracts, edge
cases, and test cases — so that an implementer (human or Cursor's AI) can build directly
against it with no ambiguity, and so "PRD complete" implies "implementation is verifiable
as correct," not just "implementation exists."

---

## Table of Contents

1. Summary
2. Problem Statement
3. Goals & Non-Goals
4. Users & User Stories
5. System Architecture
6. Module Specifications (contracts, inputs, outputs, errors)
7. Data Schemas & Validation Rules
8. Non-Functional Requirements
9. Edge Case Catalogue
10. Test Plan (unit, integration, end-to-end, manual/UAT)
11. Acceptance Criteria (per requirement, pass/fail definitions)
12. Definition of Done
13. Risk Register
14. Rollout Plan / Milestones
15. Open Questions
16. Appendix: Example Inputs & Expected Outputs

---

## 1. Summary

A local, code-based, CLI-driven pipeline that converts either (a) a user-authored,
scene-by-scene script, or (b) a single topic prompt, into one finished, captioned,
vertical (1080x1920, 9:16) talking-avatar video file. Output is a single `.mp4` the user
reviews and uploads to TikTok manually. No auto-posting in this phase.

## 2. Problem Statement

Producing AI-avatar TikTok content today requires manually chaining separate tools for
script writing, voice generation, avatar/lip-sync generation, captioning, and vertical
formatting. This PRD defines a single pipeline that performs all of these steps from one
command, with explicit, testable correctness criteria at every stage, so failures are
caught before they reach a finished video.

## 3. Goals & Non-Goals

### Goals
- **G1**: Convert a scene-list script into one finished video with correctly synced
  avatar speech per scene and burned-in captions, in the order scenes were given.
- **G2**: Support per-scene avatar persona changes, and support persona reuse (same face)
  across scenes via prompt-string matching, without redundant generation calls.
- **G3**: Support a "just give a topic" mode where the script is auto-written by Claude.
- **G4**: Fully scriptable/CLI-based; no manual GUI steps required at any stage.
- **G5**: Minimize recurring cost — TTS and captioning run locally and are free; the only
  paid calls are Muapi (avatar/lip-sync) and, in prompt mode only, Claude.
- **G6 (new in this revision)**: Every functional requirement has a corresponding
  automated or manual test with an explicit pass/fail condition, such that "all tests
  pass" is a meaningful, checkable claim.

### Non-Goals (explicitly out of scope for this phase)
- Auto-posting to TikTok or any platform.
- Multi-platform export/posting.
- GUI/dashboard.
- Voice cloning from the user's own voice (default XTTS voice only).
- Scheduling/cron orchestration.
- Analytics, hook A/B testing, or performance feedback loops.
- Guaranteeing exact per-scene duration (see §9, Edge Case E7).
- Guaranteeing Muapi/TikTok content-policy compliance of generated output (user's
  responsibility to review before upload).

## 4. Users & User Stories

**Primary user**: Zain — technical enough to run CLI commands, edit JSON/`.env` files,
and work inside Cursor.

1. As the user, I write a scene list (narration + target length + avatar description per
   scene) and run one command to get a finished video.
2. As the user, I can reuse one avatar face across all scenes by repeating the same
   `avatar_image_prompt` string, without extra generation cost or extra steps.
3. As the user, I can specify a different avatar per scene by varying that string.
4. As the user, I can instead give one topic string and get an auto-written script and
   video, when I don't want to write the script myself.
5. As the user, I get one `.mp4`, already vertical and captioned, ready to upload as-is.
6. As the user, if any stage fails, I get an error naming the failed stage, the run ID,
   and the underlying exception — not a silent failure or a generic stack trace with no
   context.
7. As the user, I can re-run a failed video without regenerating stages that already
   succeeded, where the intermediate artifacts are still valid (see §9, Edge Case E11).

## 5. System Architecture

```
scenes.json ──┐
              ├──▶ generate.py (orchestrator, CLI entrypoint)
prompt (CLI) ─┘         │
                         ├─▶ script_gen.py     [Claude API — prompt mode only]
                         ├─▶ tts_xtts.py        [local XTTS-v2]
                         ├─▶ avatar_muapi.py    [Muapi API: image-gen + lip-sync]
                         ├─▶ ffmpeg concat        [stitch per-scene clips + audio]
                         └─▶ postprocess.py     [faster-whisper + ffmpeg: captions, 9:16 crop]
                                   │
                                   ▼
                    output/<run_id>/final.mp4  (+ all intermediates retained)
```

**Execution model**: single-process, synchronous, sequential per scene. No parallelism,
no queue, no background workers in this phase (explicitly deferred — see §15).

## 6. Module Specifications

Each module below has: purpose, exact function signatures, inputs, outputs, and every
error condition it must raise (not swallow).

### 6.1 `config.py`
- **Purpose**: Load and validate environment variables.
- **Contract**: On import, must raise `KeyError` immediately (not on first use) if
  `ANTHROPIC_API_KEY` or `MUAPI_API_KEY` is missing from `.env`/environment.
- **Outputs**: typed constants (`ANTHROPIC_API_KEY: str`, `MUAPI_API_KEY: str`,
  `MUAPI_BASE_URL: str`, `OUTPUT_DIR: Path`, `LOG_DIR: Path`).
- **Directory side effects**: `OUTPUT_DIR` and `LOG_DIR` are created if absent
  (`mkdir(exist_ok=True)`), idempotent across repeated runs.

### 6.2 `script_gen.py`
- **Function**: `generate_content(niche_prompt: str) -> dict`
- **Input contract**: `niche_prompt` must be a non-empty string. Empty string is invalid
  input (see Edge Case E1).
- **Output contract**: dict with **exactly** these keys, all required, all non-empty
  after `.strip()`:
  - `script: str`
  - `caption: str` (must be ≤ 150 chars — TikTok caption practical limit used by this
    tool; API hard limit is 2200 chars but 150 is this tool's target)
  - `hashtags: list[str]` (each item must start with `#`, list length ≥ 1)
  - `avatar_image_prompt: str`
- **Failure modes that must be explicitly handled, not silently passed through**:
  - Claude response is not valid JSON → raise `ValueError` with the raw response text
    included in the message (not swallowed as a generic parse error).
  - Claude response is valid JSON but missing a required key → raise `ValueError` naming
    the missing key.
  - Network/API error (timeout, 5xx, auth failure) → propagate the underlying
    `anthropic` exception, do not catch-and-continue.

### 6.3 `tts_xtts.py`
- **Function**: `synthesize(text: str, out_path: Path, speaker_wav: str | None, language: str = "en") -> Path`
- **Input contract**: `text` non-empty after `.strip()`. `out_path` parent directory must
  exist (caller's responsibility — this function does not create parent dirs).
- **Output contract**: returns `out_path`; the file at `out_path` must exist, be a valid
  WAV file, and have duration > 0 seconds after the call returns successfully.
- **Failure modes**:
  - Empty/whitespace-only `text` → raise `ValueError` before calling the model (fail
    fast, don't waste a model call on empty input).
  - Model not yet downloaded → first call triggers download; this is allowed to be slow
    on first run but must not fail silently — if download fails, the underlying
    exception must propagate.

### 6.4 `avatar_muapi.py`
- **Functions**:
  - `generate_portrait(prompt: str, out_path: Path, model_endpoint: str) -> Path`
  - `generate_talking_video(portrait_path: Path, audio_path: Path, out_path: Path, model_endpoint: str, resolution: str) -> Path`
- **Input contract**:
  - `prompt` non-empty.
  - `portrait_path` and `audio_path` must exist on disk before the call (verified by
    caller in the orchestrator).
- **Output contract**: returned path must exist and be a non-empty file after a
  successful call. For `generate_talking_video`, output must be a valid video file whose
  duration is within a reasonable tolerance of the input audio's duration (target:
  within ±1.5 seconds, see Test T-AV-03).
- **Failure modes**:
  - Muapi returns `status: failed` on poll → raise `RuntimeError` including the full job
    response body (not just "job failed").
  - Poll exceeds `timeout_s` → raise `TimeoutError` including `request_id`, so the job
    can be looked up manually in the Muapi dashboard.
  - HTTP error (4xx/5xx) on submit or poll → `raise_for_status()` propagates; not caught.
  - **This module must NOT silently retry.** Any retry logic is the orchestrator's
    responsibility, not this module's (keeps failure surfaces predictable — see Edge
    Case E9).

### 6.5 `postprocess.py`
- **Functions**:
  - `transcribe_words(audio_path: Path) -> list[dict]` — each dict has `text: str`,
    `start: float`, `end: float`.
  - `build_srt(words: list[dict], out_srt: Path, chunk_size: int = 4) -> Path`
  - `render_final(video_path: Path, srt_path: Path, out_path: Path) -> Path`
- **Output contract**:
  - `transcribe_words` returns a list ordered by `start` ascending, with no overlapping
    ranges, and covering audio from ≥0 to ≤ the audio's total duration.
  - `render_final` output must be: exactly 1080x1920 resolution, H.264 video codec, AAC
    audio codec, and must contain burned-in subtitle text (not just a copy of the
    unmodified input — see Test T-PP-04 for how this is checked).
- **Failure modes**:
  - `transcribe_words` on silent/near-silent audio → may return an empty list; this must
    not crash `build_srt` (empty SRT is valid output, not an error) — see Edge Case E5.
  - `ffmpeg` non-zero exit code → `subprocess.run(..., check=True)` raises
    `CalledProcessError`; must include stderr in the surfaced error, not just the return
    code.

### 6.6 `generate.py` (orchestrator)
- **CLI contract**: exactly one of `--scenes <path>` or `--prompt <string>` is required;
  both together or neither is a usage error (exit code 2, argparse default behavior).
- **Scene-list mode logic**:
  1. Load and validate `scenes.json` against the schema in §7.1 before any generation
     call is made (fail fast on malformed input — see Edge Case E2).
  2. For each scene, in order: synthesize audio → resolve/generate/reuse portrait →
     generate talking video.
  3. Portrait cache keyed by **exact string equality** of `avatar_image_prompt` (not
     fuzzy/semantic matching — see Edge Case E3).
  4. Concatenate all scene videos in input order; concatenate all scene audio in the same
     order.
  5. Run captioning/formatting on the concatenated audio + video.
- **Output contract**: `output/<run_id>/final.mp4` exists, non-empty, playable, and its
  total duration approximately equals the sum of all scene audio durations (± the ±1.5s
  per-scene tolerance compounded, see Test T-E2E-02).
- **Failure propagation**: on any stage exception, the orchestrator must print which
  scene index and which stage failed before re-raising/exiting non-zero. It must not
  continue to later scenes after a failure (no partial/silently-skipped scenes in the
  final video).

## 7. Data Schemas & Validation Rules

### 7.1 `scenes.json`
```json
[
  {
    "narration": "string, required, non-empty after trim",
    "target_seconds": "number, optional, > 0 if present",
    "avatar_image_prompt": "string, required, non-empty after trim"
  }
]
```
**Validation rules (enforced before any API call is made):**
- Root must be a JSON array with length ≥ 1.
- Every element must be a JSON object (not a string/number/null).
- `narration` required, type string, non-empty after `.strip()`.
- `avatar_image_prompt` required, type string, non-empty after `.strip()`.
- `target_seconds`, if present, must be a positive number; if present but invalid
  (negative, zero, non-numeric), this is a validation error, not a silently-ignored
  field.
- Any unrecognized additional keys on a scene object are ignored, not an error (forward
  compatibility).
- Malformed JSON (fails to parse at all) → validation error naming the JSON parser's
  exact error message and line/column if available.

### 7.2 `script_gen.py` output (internal, prompt mode only)
Schema as in §6.2. This schema is Claude's structured output, validated on receipt per
the failure modes listed there.

## 8. Non-Functional Requirements

| Requirement | Target | How verified |
|---|---|---|
| No auto-posting | Zero network calls to any TikTok endpoint from this codebase in this phase | Code review / grep for "tiktok" in scope for this phase — should return nothing outside README history |
| Local-only TTS/captioning cost | Zero API spend from `tts_xtts.py` or `postprocess.py` | Code review — no `requests`/API client imports in those two files |
| Deterministic scene ordering | Output video scene order == input `scenes.json` order, always | Test T-E2E-01 |
| No partial/corrupt output on failure | If pipeline fails at any stage, no `final.mp4` is written, or an existing one from a prior successful run is not overwritten with a broken file | Test T-E2E-05 |
| Clear failure attribution | Every raised error identifies stage + (if applicable) scene index | Manual review of orchestrator's error output during induced-failure tests |
| Reproducible environment setup | `pip install -r requirements.txt` + documented `ffmpeg` install succeeds on a clean machine | Test T-ENV-01 |

## 9. Edge Case Catalogue

| ID | Scenario | Required behavior |
|---|---|---|
| E1 | `--prompt` given as empty string `""` | Validation error before any API call; do not send empty prompt to Claude |
| E2 | `scenes.json` is malformed JSON | Validation error naming the parse failure; no generation calls made |
| E3 | Two scenes have `avatar_image_prompt` differing only by trailing whitespace or case | Treated as **different** prompts (exact match only) — two portraits generated. This is intentional, documented behavior, not a bug, but must be documented clearly to the user |
| E4 | A single scene's `narration` is very long (e.g., > 40 words), producing audio far longer than the ~15s chunk assumption | `generate_talking_video` is called with the full scene audio as one unit in scene-list mode (scenes are not auto-split); if the lip-sync model rejects audio over its max duration, this surfaces as a Muapi failure naming the scene index — not a silent truncation |
| E5 | Narration produces near-silent/unintelligible TTS output, Whisper returns no words | `build_srt` produces a valid, empty SRT file; `render_final` still succeeds (video with no captions), not a crash |
| E6 | Muapi job never completes within `timeout_s` | `TimeoutError` raised with `request_id`; orchestrator halts and reports which scene, not a silent hang |
| E7 | `target_seconds` doesn't match actual TTS-produced duration | This is expected/tolerated — `target_seconds` is documented as a non-enforced pacing guide; not treated as an error condition anywhere in the code |
| E8 | User provides only 1 scene | Pipeline skips the concat step (no-op passthrough) and proceeds directly to captioning — must not fail on "concat of 1 item" |
| E9 | Muapi API returns a transient 500 mid-run | No automatic retry (per §6.4); error propagates immediately so the user can re-run rather than the pipeline silently retrying and producing unpredictable delay/cost |
| E10 | `scenes.json` path given on CLI doesn't exist | Clear `FileNotFoundError`-based message naming the missing path, before any other stage runs |
| E11 | User re-runs after a mid-pipeline failure | Each run gets a fresh `run_id`/`work_dir` (timestamp-based) — no reuse of a prior run's partial artifacts in this phase (explicitly simple; caching/resume across runs is out of scope, listed in §15) |
| E12 | `avatar_image_prompt` describes content that Muapi's models refuse/flag | Muapi failure response surfaces as-is via `RuntimeError` in `generate_portrait`; the tool does not attempt to reinterpret or retry with a modified prompt |
| E13 | ffmpeg is not installed on the machine | First ffmpeg-dependent call fails with `FileNotFoundError`/`CalledProcessError`; README must document this as a required system dependency (already does) |

## 10. Test Plan

### 10.1 Unit tests

| ID | Module | Test | Expected result |
|---|---|---|---|
| T-CFG-01 | config.py | Import with `ANTHROPIC_API_KEY` unset | Raises `KeyError` immediately |
| T-SG-01 | script_gen.py | Call with empty string prompt | Raises `ValueError` before any network call (mock the client and assert it was never called) |
| T-SG-02 | script_gen.py | Mock Claude response missing `hashtags` key | Raises `ValueError` mentioning `hashtags` |
| T-SG-03 | script_gen.py | Mock Claude response with non-JSON text | Raises `ValueError` including the raw text in the message |
| T-TTS-01 | tts_xtts.py | Call with empty string | Raises `ValueError`, no model invocation |
| T-TTS-02 | tts_xtts.py | Call with valid short text | Output file exists, is valid WAV, duration > 0 |
| T-AV-01 | avatar_muapi.py | Mock Muapi returning `status: failed` on poll | Raises `RuntimeError` containing the mocked response body |
| T-AV-02 | avatar_muapi.py | Mock Muapi never reaching `completed` within test's short `timeout_s` | Raises `TimeoutError` containing the `request_id` |
| T-AV-03 | avatar_muapi.py | Mock a successful lip-sync job; compare output duration to input audio duration | Difference ≤ 1.5s (using a stub/mock media file with known duration) |
| T-PP-01 | postprocess.py | `transcribe_words` on a known short WAV with known speech | Returned words are ordered by `start`, non-overlapping |
| T-PP-02 | postprocess.py | `transcribe_words` on silent audio | Returns `[]`, does not raise |
| T-PP-03 | postprocess.py | `build_srt` on empty word list | Produces a valid (possibly empty-body) `.srt` file, does not raise |
| T-PP-04 | postprocess.py | `render_final` on a sample video+SRT | Output resolution is exactly 1080x1920; output file size/hash differs from input (proves captions were burned in, not a no-op copy) |

### 10.2 Integration tests

| ID | Scope | Test | Expected result |
|---|---|---|---|
| T-INT-01 | script_gen → tts_xtts | Real Claude call (or recorded fixture) → feed `script` into `synthesize` | Audio file produced without error |
| T-INT-02 | tts_xtts → avatar_muapi | Real/sandbox TTS output fed into `generate_talking_video` | Video file produced, playable |
| T-INT-03 | avatar_muapi → postprocess | Concatenated video + audio fed into captioning | Final video has burned-in captions matching (roughly, spot-checked) the spoken narration |
| T-INT-04 | Portrait caching | Two scenes with identical `avatar_image_prompt` | `generate_portrait` is called exactly once (assert call count on a mock/spy), not twice |

### 10.3 End-to-end tests

| ID | Test | Expected result |
|---|---|---|
| T-E2E-01 | Run `generate.py --scenes` with a 3-scene file, each with a distinct `avatar_image_prompt` | `output/<run_id>/final.mp4` exists; visually, scene order in the video matches input order (manual spot-check of avatar/voice changes at expected timestamps) |
| T-E2E-02 | Same as above | Final video duration ≈ sum of the three scenes' individual TTS-produced audio durations, within a few seconds of tolerance (accounting for per-scene lip-sync duration variance from T-AV-03) |
| T-E2E-03 | Run with `--prompt "..."` | `output/<run_id>/final.mp4` exists; script/caption/hashtags were generated (visible in `content.json` in the run folder); video is captioned and 9:16 |
| T-E2E-04 | Run with a `scenes.json` where two scenes share the exact same `avatar_image_prompt` | Only one portrait file (`portrait_00.png`) exists in the run folder — proves caching worked end-to-end, not just at the mocked unit level |
| T-E2E-05 | Induce a failure mid-run (e.g., invalid Muapi key) | No `final.mp4` is produced; error output names the failed scene and stage; process exits non-zero |
| T-E2E-06 | Run with a single-scene input (Edge Case E8) | Succeeds without a concat step failure; output is a valid captioned video |
| T-E2E-07 | Run with malformed `scenes.json` (Edge Case E2) | Fails immediately with a validation error; **zero** Muapi/Claude/XTTS calls are made (verify via mock call-count assertions or, for a real run, via absence of any generated audio/portrait/video files in the run folder) |

### 10.4 Manual / UAT (user acceptance testing)

| ID | Test | Pass condition (subjective, user-judged) |
|---|---|---|
| T-UAT-01 | Watch a full generated video start to finish | Avatar's mouth movement is plausibly synced to the audio (not perfect lip-sync required, but not obviously desynced) |
| T-UAT-02 | Read the burned-in captions while watching | Captions are legible (font size/contrast), roughly track the spoken words in real time, no major dropped/garbled sections |
| T-UAT-03 | Check video framing | Avatar is reasonably centered/visible in the 9:16 crop, not cut off or off-frame due to the crop step |
| T-UAT-04 | Compare avatar persona across scenes (same-prompt case) | Same face/appearance is visibly consistent across scenes using the identical `avatar_image_prompt` |
| T-UAT-05 | Compare avatar persona across scenes (different-prompt case) | Visibly different persona per scene, matching the intent of each distinct prompt |
| T-UAT-06 | Overall runtime for a 3-scene video | Subjectively acceptable for a manual, on-demand workflow (no hard SLA in this phase, but flag if it's frustratingly slow — informs whether M5/parallelism gets prioritized) |

## 11. Acceptance Criteria (per goal)

- **G1 met** when T-E2E-01, T-E2E-02, and T-UAT-01/02/03 all pass.
- **G2 met** when T-INT-04, T-E2E-04, T-UAT-04, and T-UAT-05 all pass.
- **G3 met** when T-E2E-03 passes.
- **G4 met** when the entire pipeline (all E2E tests) can be run from a single CLI
  invocation with no manual GUI interaction at any point.
- **G5 met** when a code review confirms `tts_xtts.py` and `postprocess.py` make no
  network calls, and Muapi/Claude usage is limited to the documented calls per run.
- **G6 met** when every functional requirement in §6 has at least one corresponding test
  in §10, and every edge case in §9 has at least one corresponding test or an explicit
  note on why it's covered by code review instead (e.g., E13's ffmpeg dependency, which
  is an environment check, not a unit-testable code path).

## 12. Definition of Done

A given version of this pipeline is "done" for this phase when **all** of the following
are true:

1. All unit tests (§10.1) pass.
2. All integration tests (§10.2) pass against either live APIs or recorded fixtures.
3. All end-to-end tests (§10.3) pass on a clean environment (fresh venv, per §10 T-ENV-01
   style clean install).
4. All manual/UAT checks (§10.4) are performed at least once by the user and judged
   acceptable.
5. Every edge case in §9 has been either explicitly tested or explicitly and knowingly
   accepted as documented behavior (not silently unhandled).
6. README accurately reflects current CLI usage, `.env` requirements, and system
   dependencies (ffmpeg), verified by literally following it on a clean machine.
7. No TODO/placeholder code paths remain in the modules covering G1–G5 (script_gen,
   tts_xtts, avatar_muapi, postprocess, generate.py).

**Explicit caveat**: "PRD complete and all tests passing" means the *pipeline behaves
exactly as specified here*, including its known, documented limitations (approximate
duration control, exact-string avatar caching, no auto-retry, no auto-posting). It does
not mean the pipeline is free of *all possible* issues outside this spec's scope — e.g.,
Muapi model quality/output aesthetics, TikTok content-policy compliance of generated
video, or Muapi's own uptime, are outside what this PRD or its tests can guarantee.

## 13. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Muapi endpoint names/params differ from what's coded (`flux-dev`, `infinitetalk-image-to-video` are unverified guesses) | High until verified | Pipeline fails at first real Muapi call | M2 in rollout plan: verify against live Muapi docs before any other testing |
| Lip-sync model's actual max clip duration is lower/higher than assumed ~15s | Medium | Long single-scene narration could fail or silently truncate | Edge Case E4 behavior (fail loud, not silent truncation) + T-AV-03 duration check |
| XTTS output duration is unpredictable relative to `target_seconds` | High (by design) | Pacing won't exactly match user's plan | Documented as non-goal/known limitation (Edge Case E7), not treated as a bug |
| ffmpeg subtitle burn-in styling unreadable on some background videos | Medium | Fails T-UAT-02 | Manual UAT check is the catch; style constants (font size, outline, box) are adjustable in `postprocess.py` if flagged |
| Local machine lacks GPU/enough RAM for XTTS at reasonable speed | Low-Medium | Slow generation, poor UAT-06 result | Not solved in this phase; noted as a possible reason to move TTS to a cloud box later |

## 14. Rollout Plan / Milestones

1. **M1 — Environment setup**: Cursor project scaffolded, dependencies installed, `.env`
   populated, XTTS pre-downloaded. *(Test: T-ENV-01)*
2. **M2 — Muapi verification**: confirm real endpoint names/params against live
   dashboard/docs; update `avatar_muapi.py`. *(Blocks T-AV-*, T-INT-02/03, all T-E2E)*
3. **M3 — Unit + integration test pass**: implement and pass §10.1 and §10.2.
4. **M4 — First end-to-end test run**: `scenes.example.json` through the full pipeline,
   producing a working `final.mp4`; run §10.3 and §10.4 against it.
5. **M5 — Real content run**: user's own scene script run through the validated
   pipeline, manually reviewed and uploaded to TikTok.
6. **M6 (future/optional, out of current scope)**: revisit auto-upload (TikTok Content
   Posting API), scheduling/cron, parallel scene generation, voice cloning, and
   resume-from-failure caching (Edge Case E11) once the manual workflow above is
   validated.

## 15. Open Questions

- Should `target_seconds` eventually drive an XTTS speaking-rate parameter to get closer
  to enforced timing, or remain a non-enforced guide indefinitely? (Currently: non-goal,
  see Edge Case E7 — revisit only if UAT-06 or real usage makes this a recurring pain
  point.)
- Should avatar caching move from exact-string matching to a user-supplied explicit
  `avatar_id` field instead, to avoid accidental duplicate-portrait generation from
  near-identical-but-not-identical prompt strings (Edge Case E3)? Deferred until it's
  caused a real problem.
- Is resume-from-failure (Edge Case E11) worth building before M6, given API costs from
  re-running successful early scenes on every retry? Currently deferred but flagged as
  the most likely near-term addition if Muapi failures turn out to be frequent.

## 16. Appendix: Example Inputs & Expected Outputs

### 16.1 Valid 3-scene input (used in T-E2E-01/02/04)
See `scripts/scenes.example.json` in the repo — 3 scenes, 2 distinct
`avatar_image_prompt` values (scenes 1–2 share one, scene 3 uses another), used to
exercise both the caching path (E3/T-INT-04) and the persona-change path (G2) in one
fixture.

### 16.2 Invalid input examples (used in T-E2E-07 and unit tests)
- `[]` — empty array. Should fail validation (root array length ≥ 1 required).
- `[{"narration": ""}]` — missing `avatar_image_prompt`, empty `narration`. Should fail
  validation on both grounds; error message should name at least the first violation
  found.
- `[{"narration": "hi", "avatar_image_prompt": "x", "target_seconds": -5}]` — invalid
  negative `target_seconds`. Should fail validation per §7.1's rule that a *present*
  `target_seconds` must be positive.
