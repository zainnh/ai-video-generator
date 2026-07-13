"""Local Flask web UI for the avatar video pipeline."""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from scripts.generate import PipelineError, generate_from_scenes, validate_scenes
from scripts.config import OUTPUT_DIR, WEB_PORT

APP_DIR = Path(__file__).resolve().parent
EXAMPLE_SCENES_PATH = APP_DIR / "scenes.example.json"

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

STAGE_LABELS = {
    "tts": "Generating voices",
    "portrait": "Generating portraits",
    "talking_video": "Generating lip sync videos",
    "postprocess": "Adding captions and formatting",
}


def _load_example_scenes_json() -> str:
    return EXAMPLE_SCENES_PATH.read_text(encoding="utf-8")


def _new_run_dir() -> tuple[str, Path]:
    from datetime import datetime, timezone

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    work_dir = OUTPUT_DIR / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    return run_id, work_dir


def _update_job(job_id: str, **fields) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def _run_job(job_id: str, scenes: list[dict], run_id: str, work_dir: Path) -> None:
    def progress_cb(stage: str, done: int, total: int) -> None:
        _update_job(
            job_id,
            stage=stage,
            done=done,
            total=total,
            stage_label=STAGE_LABELS.get(stage, stage),
        )

    try:
        (work_dir / "scenes.json").write_text(
            json.dumps(scenes, indent=2), encoding="utf-8"
        )
        final_path = generate_from_scenes(
            scenes, work_dir, run_id, progress_cb=progress_cb
        )
        _update_job(
            job_id,
            status="complete",
            final_path=str(final_path),
            run_id=run_id,
            stage="done",
            stage_label="Complete",
            done=1,
            total=1,
        )
    except PipelineError as exc:
        _update_job(
            job_id,
            status="failed",
            error=str(exc),
            failed_stage=exc.stage,
            failed_scene=exc.scene_index,
        )
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))


@app.get("/")
def index():
    return render_template("index.html", example_json=_load_example_scenes_json())


@app.post("/generate")
def generate():
    raw = request.get_data(as_text=True)
    if not raw or not raw.strip():
        return jsonify({"error": "Request body must contain scenes JSON"}), 400

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Invalid JSON: {exc.msg}"}), 400

    try:
        scenes = validate_scenes(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = str(uuid.uuid4())
    run_id, work_dir = _new_run_dir()

    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "stage": "starting",
            "stage_label": "Starting",
            "done": 0,
            "total": len(scenes),
            "run_id": run_id,
            "final_path": None,
            "error": None,
            "failed_stage": None,
            "failed_scene": None,
        }

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, scenes, run_id, work_dir),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.get("/status/<job_id>")
def status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.get("/result/<job_id>")
def result(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return "Job not found", 404
    if job["status"] != "complete":
        return redirect(url_for("index"))
    return render_template(
        "result.html",
        job_id=job_id,
        final_path=job["final_path"],
        run_id=job["run_id"],
    )


@app.get("/video/<job_id>")
def video(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job["status"] != "complete" or not job.get("final_path"):
        return "Video not found", 404

    video_path = Path(job["final_path"])
    if not video_path.exists():
        return "Video file missing", 404

    def generate():
        with video_path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk

    return Response(generate(), mimetype="video/mp4")


if __name__ == "__main__":
    url = f"http://localhost:{WEB_PORT}"
    print(f"\n  Avatar Video UI → {url}\n")
    app.run(host="127.0.0.1", port=WEB_PORT, debug=False, threaded=True)
