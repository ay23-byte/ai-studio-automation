"""Universal documentary visual-production QA.

Creates a per-shot manifest and sampled-frame contact sheets. Metadata scores and
frame extraction are evidence for editorial review, not proof of semantic match,
historical authenticity, licensing, or lip-sync.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _source_metadata(media_path: Path) -> dict:
    sidecar = media_path.with_suffix(media_path.suffix + ".source.json")
    if not sidecar.is_file():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sample_frames(media_path: Path, out_dir: Path, shot_id: str, count: int = 3) -> list[str]:
    """Extract evenly spaced stills from a video using ffmpeg; images are copied as samples."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)
        ], stderr=subprocess.DEVNULL, text=True).strip()
        duration = max(float(raw), 0.1)
    except Exception:
        return []
    samples = []
    for index in range(count):
        timestamp = duration * (index + 1) / (count + 1)
        target = out_dir / f"{shot_id}_frame_{index + 1}.jpg"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(media_path),
                "-frames:v", "1", "-q:v", "3", str(target)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=45)
            if target.is_file() and target.stat().st_size > 1000:
                samples.append(str(target))
        except Exception:
            continue
    return samples


def build_visual_review(shot_timeline: list[dict], build_dir: str | Path,
                        sample_video_frames: bool = True) -> dict:
    """Record shot-level prompts, media provenance, duration and review requirements."""
    root = Path(build_dir)
    frame_dir = root / "visual_review_frames"
    shots, issues = [], []
    previous_media = None
    for index, shot in enumerate(shot_timeline):
        media = shot.get("_media") or {}
        raw_path = media.get("path")
        path = Path(raw_path) if raw_path else None
        source = _source_metadata(path) if path and path.is_file() else {}
        media_type = media.get("type", "unknown")
        shot_id = f"shot_{index + 1:04d}"
        prompt = str(shot.get("visual_prompt") or shot.get("image") or
                     shot.get("title") or shot.get("narration") or "")
        frames = []
        if path and path.is_file() and media_type == "video" and sample_video_frames:
            frames = _sample_frames(path, frame_dir, shot_id)
        elif path and path.is_file() and media_type == "image":
            frames = [str(path)]
        if not path or not path.is_file():
            issues.append({"level": "error", "shot": shot_id, "message": "Media file is missing."})
        if path and str(path) == previous_media:
            issues.append({"level": "warning", "shot": shot_id, "message": "The same media file is reused in consecutive shots."})
        previous_media = str(path) if path else None
        if media_type == "video" and not frames:
            issues.append({"level": "warning", "shot": shot_id, "message": "Could not extract sample frames; visual inspection is incomplete."})
        if source.get("relevance_score") is not None and float(source["relevance_score"]) < 0.12:
            issues.append({"level": "error", "shot": shot_id, "message": "Weak metadata relevance; replace this asset."})
        if not source:
            issues.append({"level": "warning", "shot": shot_id, "message": "No source metadata sidecar; provenance and match cannot be assessed automatically."})
        shots.append({
            "shot_id": shot_id, "start_seconds": shot.get("_start"),
            "duration_seconds": shot.get("_dur"), "media_type": media_type,\n            "visual_type": shot.get("visual_type", "unknown"),\n            "subject": shot.get("subject", ""), "location": shot.get("location", ""),\n            "time_period": shot.get("time_period", ""),\n            "shot_size": shot.get("shot_size", ""),\n            "camera_movement": shot.get("camera_movement", ""),\n            "visual_guardrail": shot.get("visual_guardrail", ""),
            "media_path": str(path) if path else None, "visual_prompt": prompt[:700],
            "narration": str(shot.get("narration") or "")[:700],
            "source": source, "sample_frames": frames,
            "semantic_match_verified": False, "historical_authenticity_verified": False,
            "human_visual_review_required": True,
        })
    report = {
        "shot_count": len(shots), "shots": shots, "issues": issues,
        "automated_vision_verification": False,
        "shot_type_counts": {kind: sum(1 for shot in shots if shot.get("visual_type") == kind)\n                             for kind in sorted({shot.get("visual_type", "unknown") for shot in shots})},
        "historical_authenticity_verified": False,
        "publication_ready": False,
        "notice": "Frame samples and metadata support review only. A human must confirm every visual matches the narration, verify historical context and asset rights, and approve AI-reconstruction disclosures.",
    }
    output = root / "visual_review_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
