"""CLI orchestrator for the AI avatar TikTok video pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts import avatar_muapi, postprocess, script_gen, tts_xtts
from scripts.config import (
    MUAPI_LIPSYNC_ENDPOINT,
    MUAPI_PORTRAIT_ENDPOINT,
    OUTPUT_DIR,
)

ProgressCallback = Callable[[str, int, int], None]


class PipelineError(Exception):
    """Pipeline failure with stage and optional scene attribution."""

    def __init__(self, stage: str, message: str, scene_index: int | None = None):
        self.stage = stage
        self.scene_index = scene_index
        super().__init__(message)


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


def validate_scenes(data: object, source: str = "scenes") -> list[dict]:
    """Validate scenes JSON against PRD §7.1 schema."""
    if not isinstance(data, list):
        raise ValueError(f"{source}: root must be a JSON array")
    if len(data) < 1:
        raise ValueError(f"{source}: array must contain at least one scene")

    validated: list[dict] = []
    for index, scene in enumerate(data):
        prefix = f"{source}[{index}]"
        if not isinstance(scene, dict):
            raise ValueError(f"{prefix}: each scene must be a JSON object")

        if "narration" not in scene:
            raise ValueError(f"{prefix}: missing required field 'narration'")
        narration = scene["narration"]
        if not isinstance(narration, str) or not narration.strip():
            raise ValueError(f"{prefix}: 'narration' must be a non-empty string")

        if "avatar_image_prompt" not in scene:
            raise ValueError(f"{prefix}: missing required field 'avatar_image_prompt'")
        avatar_prompt = scene["avatar_image_prompt"]
        if not isinstance(avatar_prompt, str) or not avatar_prompt.strip():
            raise ValueError(
                f"{prefix}: 'avatar_image_prompt' must be a non-empty string"
            )

        if "target_seconds" in scene:
            target = scene["target_seconds"]
            if not isinstance(target, (int, float)) or target <= 0:
                raise ValueError(
                    f"{prefix}: 'target_seconds' must be a positive number when present"
                )

        validated.append(
            {
                "narration": narration.strip(),
                "avatar_image_prompt": avatar_prompt.strip(),
                **(
                    {"target_seconds": float(scene["target_seconds"])}
                    if "target_seconds" in scene
                    else {}
                ),
            }
        )

    return validated


def load_scenes(path: Path) -> list[dict]:
    """Load and validate a scenes JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scenes file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        location = ""
        if exc.lineno:
            location = f" at line {exc.lineno}, column {exc.colno}"
        raise ValueError(
            f"Failed to parse scenes JSON{location}: {exc.msg}"
        ) from exc

    return validate_scenes(raw, source=str(path))


def _concat_videos(video_paths: list[Path], out_path: Path) -> Path:
    if len(video_paths) == 1:
        return video_paths[0]

    list_file = out_path.parent / "concat_list.txt"
    lines = [f"file '{p.resolve()}'" for p in video_paths]
    list_file.write_text("\n".join(lines), encoding="utf-8")

    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(out_path),
        ]
    )
    return out_path


def _extract_audio(video_path: Path, audio_path: Path) -> Path:
    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio_path),
        ]
    )
    return audio_path


def _new_run_dir() -> tuple[str, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    work_dir = OUTPUT_DIR / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    return run_id, work_dir


def _report_failure(
    run_id: str, stage: str, scene_index: int | None, exc: BaseException
) -> None:
    scene_part = f", scene {scene_index}" if scene_index is not None else ""
    print(
        f"\n[PIPELINE FAILED] run_id={run_id}, stage={stage}{scene_part}",
        file=sys.stderr,
    )
    print(f"Error: {exc}", file=sys.stderr)


def _tick_progress(
    progress_cb: ProgressCallback | None,
    lock: threading.Lock,
    counter: list[int],
    stage: str,
    total: int,
) -> None:
    if not progress_cb:
        return
    with lock:
        counter[0] += 1
        progress_cb(stage, counter[0], total)


def generate_from_scenes(
    scenes: list[dict],
    work_dir: Path,
    run_id: str,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    """Execute the pipeline with parallel TTS, portraits, and lip-sync stages."""
    del run_id  # reserved for callers; artifacts live in work_dir
    n_scenes = len(scenes)
    progress_lock = threading.Lock()
    tts_done: list[int] = [0]
    portrait_done: list[int] = [0]
    talking_done: list[int] = [0]

    audio_paths: dict[int, Path] = {}

    def synthesize_scene(scene_index: int, scene: dict) -> tuple[int, Path]:
        prefix = f"scene_{scene_index:02d}"
        audio_path = work_dir / f"{prefix}.wav"
        tts_xtts.synthesize(scene["narration"], audio_path, speaker_wav=None)
        return scene_index, audio_path

    with ThreadPoolExecutor(max_workers=n_scenes) as executor:
        futures = {
            executor.submit(synthesize_scene, i, scene): i
            for i, scene in enumerate(scenes)
        }
        for future in as_completed(futures):
            scene_index = futures[future]
            try:
                idx, path = future.result()
                audio_paths[idx] = path
                _tick_progress(progress_cb, progress_lock, tts_done, "tts", n_scenes)
            except Exception as exc:
                raise PipelineError("tts", str(exc), scene_index) from exc

    prompt_to_portrait: dict[str, Path] = {}
    portrait_counter = 0
    for scene in scenes:
        prompt = scene["avatar_image_prompt"]
        if prompt not in prompt_to_portrait:
            prompt_to_portrait[prompt] = work_dir / f"portrait_{portrait_counter:02d}.png"
            portrait_counter += 1

    unique_prompts = list(prompt_to_portrait.keys())
    n_portraits = len(unique_prompts)

    def generate_one_portrait(prompt: str) -> tuple[str, Path]:
        out_path = prompt_to_portrait[prompt]
        avatar_muapi.generate_portrait(prompt, out_path, MUAPI_PORTRAIT_ENDPOINT)
        return prompt, out_path

    with ThreadPoolExecutor(max_workers=max(n_portraits, 1)) as executor:
        futures = {
            executor.submit(generate_one_portrait, prompt): prompt
            for prompt in unique_prompts
        }
        for future in as_completed(futures):
            prompt = futures[future]
            try:
                future.result()
                _tick_progress(
                    progress_cb, progress_lock, portrait_done, "portrait", n_portraits
                )
            except Exception as exc:
                scene_index = next(
                    (i for i, s in enumerate(scenes) if s["avatar_image_prompt"] == prompt),
                    None,
                )
                raise PipelineError("portrait", str(exc), scene_index) from exc

    scene_videos: dict[int, Path] = {}

    def generate_one_talking_video(scene_index: int, scene: dict) -> tuple[int, Path]:
        prefix = f"scene_{scene_index:02d}"
        portrait_path = prompt_to_portrait[scene["avatar_image_prompt"]]
        audio_path = audio_paths[scene_index]
        scene_video_path = work_dir / f"{prefix}.mp4"
        avatar_muapi.generate_talking_video(
            portrait_path,
            audio_path,
            scene_video_path,
            MUAPI_LIPSYNC_ENDPOINT,
            resolution="720p",
        )
        return scene_index, scene_video_path

    with ThreadPoolExecutor(max_workers=n_scenes) as executor:
        futures = {
            executor.submit(generate_one_talking_video, i, scene): i
            for i, scene in enumerate(scenes)
        }
        for future in as_completed(futures):
            scene_index = futures[future]
            try:
                idx, path = future.result()
                scene_videos[idx] = path
                _tick_progress(
                    progress_cb, progress_lock, talking_done, "talking_video", n_scenes
                )
            except Exception as exc:
                raise PipelineError("talking_video", str(exc), scene_index) from exc

    ordered_videos = [scene_videos[i] for i in range(n_scenes)]

    try:
        if progress_cb:
            progress_cb("postprocess", 0, 1)

        stitched_path = work_dir / "stitched.mp4"
        if len(ordered_videos) == 1:
            stitched = ordered_videos[0]
        else:
            stitched = _concat_videos(ordered_videos, stitched_path)

        audio_for_captions = work_dir / "stitched_audio.wav"
        _extract_audio(stitched, audio_for_captions)

        words = postprocess.transcribe_words(audio_for_captions)
        srt_path = work_dir / "captions.srt"
        postprocess.build_srt(words, srt_path)

        final_path = work_dir / "final.mp4"
        postprocess.render_final(stitched, srt_path, final_path)

        if progress_cb:
            progress_cb("postprocess", 1, 1)
    except Exception as exc:
        raise PipelineError("postprocess", str(exc)) from exc

    return final_path


def run_pipeline(scenes: list[dict], work_dir: Path, run_id: str) -> Path:
    """Execute the full pipeline for a list of validated scenes."""
    return generate_from_scenes(scenes, work_dir, run_id, progress_cb=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Avatar TikTok video generation pipeline"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenes", type=Path, help="Path to scenes JSON file")
    group.add_argument(
        "--prompt",
        type=str,
        help="Topic prompt for auto-generated script (prompt mode)",
    )
    args = parser.parse_args(argv)

    run_id = "unknown"
    try:
        if args.prompt is not None and not args.prompt.strip():
            print("Error: --prompt must be a non-empty string", file=sys.stderr)
            return 2

        run_id, work_dir = _new_run_dir()

        if args.scenes is not None:
            scenes = load_scenes(args.scenes)
            (work_dir / "scenes.json").write_text(
                json.dumps(scenes, indent=2), encoding="utf-8"
            )
        else:
            content = script_gen.generate_content(args.prompt)
            (work_dir / "content.json").write_text(
                json.dumps(content, indent=2), encoding="utf-8"
            )
            scenes = [
                {
                    "narration": content["script"],
                    "avatar_image_prompt": content["avatar_image_prompt"],
                }
            ]

        final_path = run_pipeline(scenes, work_dir, run_id)
        print(f"Done. run_id={run_id}")
        print(f"Output: {final_path}")
        return 0

    except PipelineError as exc:
        _report_failure(run_id, exc.stage, exc.scene_index, exc)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        _report_failure(run_id, "validation", None, exc)
        return 1
    except Exception as exc:
        _report_failure(run_id, "unknown", None, exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
