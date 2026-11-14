"""Shot-to-narration semantic QA for rendered news Shorts.

This stage uses Gemini Vision only as a conservative relevance screen. It never
verifies factual truth, historical authenticity, identities, copyright or licences.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.visual_semantics import verify_shot_semantics
from core.ffmpeg_utils import ffmpeg_bin


def _sample_midpoint(video: Path, seconds: float, output: Path) -> str | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "3", str(output)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if result.returncode == 0 and output.is_file() and output.stat().st_size > 1000:
        return str(output)
    return None


def run_semantic_qa(video: str | Path, manifest_path: str | Path,
                    output_dir: str | Path) -> dict[str, Any]:
    """Check one midpoint frame per manifest scene against that scene's narration."""
    video, manifest_path, output_dir = Path(video), Path(manifest_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report = {"status": "ERROR", "reason": f"Cannot read production manifest: {type(exc).__name__}: {exc}",
                  "shots": [], "human_review_required": True}
        (output_dir / "semantic_qa_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    scenes = manifest.get("shots")
    if not isinstance(scenes, list) or not scenes:
        report = {"status": "UNAVAILABLE", "reason": "Manifest has no per-shot narration and visual descriptions.",
                  "shots": [], "human_review_required": True}
        (output_dir / "semantic_qa_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    rows = []
    cursor = 0.0
    for index, scene in enumerate(scenes, 1):
        try:
            start = float(scene.get("start_seconds", cursor))
            duration = float(scene.get("duration_seconds", 0))
            if duration <= 0:
                raise ValueError("duration_seconds must be positive")
            midpoint = start + duration / 2
            frame = _sample_midpoint(video, midpoint, output_dir / "semantic_frames" / f"shot_{index:02d}.jpg")
            if not frame:
                result = {"status": "error", "reason": "Could not extract a midpoint frame.",
                          "human_review_required": True}
            else:
                result = verify_shot_semantics({
                    "narration": scene.get("narration", ""),
                    "visual_prompt": scene.get("visual_prompt", ""),
                    "visual_type": scene.get("visual_type", "unknown"),
                    "sample_frames": [frame],
                })
            rows.append({"shot_id": str(scene.get("shot_id") or f"shot_{index:02d}"),
                         "start_seconds": round(start, 3), "duration_seconds": round(duration, 3),
                         "frame": frame, **result})
            cursor = start + duration
        except Exception as exc:
            rows.append({"shot_id": str(scene.get("shot_id") or f"shot_{index:02d}"),
                         "status": "error", "reason": f"{type(exc).__name__}: {exc}",
                         "human_review_required": True})

    statuses = [str(row.get("status", "error")).lower() for row in rows]
    if not rows or any(status in {"mismatch", "fail"} for status in statuses):
        status = "FAIL"
    elif any(status != "match" for status in statuses):
        status = "REVIEW"
    else:
        status = "PASS"
    report = {
        "status": status,
        "shot_count": len(rows),
        "match_count": statuses.count("match"),
        "mismatch_count": statuses.count("mismatch"),
        "uncertain_count": statuses.count("uncertain"),
        "unavailable_count": statuses.count("unavailable"),
        "error_count": statuses.count("error"),
        "shots": rows,
        "human_review_required": True,
        "semantic_match_verified": status == "PASS",
        "notice": "AI semantic screening is fallible. PASS means only that sampled frames appear aligned with their scene descriptions; it does not establish factual truth, authenticity, identity, rights or licensing. Human review remains mandatory.",
    }
    (output_dir / "semantic_qa_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
