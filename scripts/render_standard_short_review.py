"""Render a supplied Shorts script to a review-only MP4 artifact.

This entry point deliberately has no YouTube upload path. The script JSON and
render outputs are retained together for human review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.db import save_video
from core.pipeline import build_shorts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="Path to a reviewed scene-based script JSON")
    parser.add_argument("--language", choices=("english", "hindi"), default="english")
    parser.add_argument("--voice-gender", choices=("male", "female"), default="male")
    parser.add_argument("--output-dir", default="output/standard_shorts")
    args = parser.parse_args()

    script_path = Path(args.script)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if not isinstance(script, dict):
        raise ValueError("Script JSON must contain an object.")
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Script must contain a non-empty scenes array.")
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict) or not str(scene.get("say") or "").strip():
            raise ValueError(f"Scene {index} must contain non-empty 'say' narration.")

    title = str(script.get("title") or "Untitled Shorts review").strip()
    job_id = save_video(title, "shorts", args.language, script.get("category", "general"), script)
    build_dir = Path("build") / f"job_{job_id}"
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (build_dir / "review_status.json").write_text(json.dumps({
        "status": "REVIEW_REQUIRED",
        "job_id": job_id,
        "title": title,
        "human_review_required": True,
        "youtube_upload_attempted": False,
        "publication_eligible": False,
        "notice": "This is a review render only. Inspect the complete MP4, captions, narration, factual claims, visual relevance, and all asset rights before any publication."
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    video = build_shorts(
        script, job_id, build_dir=build_dir,
        language=args.language, voice_gender=args.voice_gender,
    )
    destination = Path(args.output_dir) / f"shorts_{job_id}"
    destination.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(video, destination / "video.mp4")
    shutil.copy2(build_dir / "subs.ass", destination / "captions.ass")
    shutil.copy2(build_dir / "script.json", destination / "script.json")
    shutil.copy2(build_dir / "quality_report.json", destination / "quality_report.json")
    shutil.copy2(build_dir / "review_status.json", destination / "review_status.json")
    for optional in ("cinematic_shot_plan.json", "audio_quality_report.json"):
        source = build_dir / optional
        if source.is_file():
            shutil.copy2(source, destination / optional)
    print(json.dumps({
        "status": "REVIEW_REQUIRED",
        "job_id": job_id,
        "video": str(destination / "video.mp4"),
        "review_bundle": str(destination),
        "youtube_upload_attempted": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
