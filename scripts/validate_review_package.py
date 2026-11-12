"""Fail-closed validator for the shared AI Studio review-package contract.

This validates package structure and technical metadata. It cannot judge visual
relevance, factual accuracy, media ownership, or cinematic quality.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

MODES = {"longform": (16 / 9, 0.08), "shortform": (9 / 16, 0.08), "daily_news": (9 / 16, 0.08)}
QA_FILES = {
    "technical": "qa/technical.json",
    "semantic": "qa/semantic.json",
    "audio": "qa/audio.json",
    "captions": "qa/captions.json",
    "assets": "qa/assets.json",
}
REVIEW_FIELDS = ("editorial", "factual", "visual", "audio", "rights", "disclosure")


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON from {path}: {exc}") from exc


def _inside(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate != root.resolve() and root.resolve() not in candidate.parents:
        raise ValueError(f"Package path escapes package directory: {relative}")
    return candidate


def _probe(video: Path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if result.returncode:
        raise ValueError("ffprobe could not decode the MP4; file is not verified playable.")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON.") from exc
    streams = data.get("streams", [])
    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) < 1:
        raise ValueError("Expected exactly one video stream and at least one audio stream.")
    video_stream = videos[0]
    width, height = int(video_stream.get("width") or 0), int(video_stream.get("height") or 0)
    if not width or not height:
        raise ValueError("Video stream has invalid dimensions.")
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise ValueError("MP4 duration is missing or invalid.")
    return {"width": width, "height": height, "duration_seconds": round(duration, 3),
            "video_codec": video_stream.get("codec_name"),
            "audio_codecs": sorted({item.get("codec_name", "unknown") for item in audios})}


def validate_package(package_dir: str | Path) -> dict:
    root = Path(package_dir).resolve()
    errors, warnings, required_reviews = [], [], []
    if not root.is_dir():
        return {"status": "FAIL", "errors": [f"Package directory does not exist: {root}"],
                "warnings": [], "required_human_reviews": []}
    manifest_path = root / "production_manifest.json"
    checklist_path = root / "review_checklist.json"
    if not manifest_path.is_file():
        return {"status": "FAIL", "errors": ["Missing production_manifest.json"],
                "warnings": [], "required_human_reviews": []}
    try:
        manifest = _load_json(manifest_path)
    except ValueError as exc:
        return {"status": "FAIL", "errors": [str(exc)], "warnings": [], "required_human_reviews": []}

    mode = manifest.get("mode")
    if manifest.get("schema_version") != 1:
        errors.append("production_manifest.json schema_version must be 1.")
    if mode not in MODES:
        errors.append("mode must be longform, shortform, or daily_news.")
    if not str(manifest.get("topic") or "").strip():
        errors.append("Manifest topic is missing.")
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        errors.append("Manifest must contain at least one scene.")
        scenes = []

    try:
        video_rel = manifest.get("video_file") or "video.mp4"
        video = _inside(root, video_rel)
        if not video.is_file() or video.stat().st_size < 1024:
            raise ValueError(f"Missing or undersized video file: {video_rel}")
        probe = _probe(video)
        if mode in MODES:
            expected, tolerance = MODES[mode]
            actual = probe["width"] / probe["height"]
            if abs(actual - expected) / expected > tolerance:
                errors.append(f"Wrong aspect ratio for {mode}: {probe['width']}x{probe['height']}.")
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        probe = None
        errors.append(str(exc))

    try:
        captions_rel = manifest.get("captions_file") or "captions.srt"
        captions = _inside(root, captions_rel)
        if not captions.is_file() or captions.stat().st_size == 0:
            errors.append(f"Missing or empty captions artifact: {captions_rel}")
    except ValueError as exc:
        errors.append(str(exc))

    seen_scene_ids = set()
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"Scene {index + 1} must be an object.")
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id or scene_id in seen_scene_ids:
            errors.append(f"Scene {index + 1} has a missing or duplicate scene_id.")
        seen_scene_ids.add(scene_id)
        if not str(scene.get("narration") or "").strip():
            errors.append(f"Scene {scene_id or index + 1} has no narration.")
        assets = scene.get("assets")
        if not isinstance(assets, list) or not assets:
            errors.append(f"Scene {scene_id or index + 1} has no asset records.")
            continue
        for asset_index, asset in enumerate(assets):
            label = f"scene {scene_id or index + 1} asset {asset_index + 1}"
            if not isinstance(asset, dict):
                errors.append(f"{label} must be an object.")
                continue
            try:
                asset_path = _inside(root, str(asset.get("path") or ""))
                if not asset_path.is_file():
                    errors.append(f"{label} file is missing: {asset.get('path')!r}")
            except ValueError as exc:
                errors.append(f"{label}: {exc}")
            provenance = asset.get("provenance")
            if not isinstance(provenance, dict) or provenance.get("origin") not in {"source", "ai_generated", "user_provided", "unknown"}:
                errors.append(f"{label} has missing/invalid provenance.")
            elif provenance.get("origin") == "source" and not provenance.get("source_url"):
                errors.append(f"{label} source asset has no source_url.")
            elif provenance.get("origin") == "ai_generated" and not provenance.get("prompt"):
                errors.append(f"{label} AI asset has no generation prompt.")
            elif provenance.get("origin") == "unknown":
                required_reviews.append(f"Confirm origin and provenance for {label}.")
            rights = asset.get("rights")
            if not isinstance(rights, dict) or rights.get("status") != "reviewed" or not rights.get("license"):
                required_reviews.append(f"Confirm rights/licence for {label}.")
            if not str(asset.get("asset_type") or "").strip():
                errors.append(f"{label} has no asset_type.")

    qa_results = {}
    for name, relative in QA_FILES.items():
        path = root / relative
        if not path.is_file():
            required_reviews.append(f"Missing {relative}.")
            qa_results[name] = "NOT_RUN"
            continue
        try:
            report = _load_json(path)
            status = str(report.get("status", "NOT_RUN")).upper()
            qa_results[name] = status
            if status == "FAIL":
                errors.append(f"{relative} reports FAIL.")
            elif status != "PASS":
                required_reviews.append(f"{relative} is {status}; review required.")
        except ValueError as exc:
            errors.append(str(exc))

    if not checklist_path.is_file():
        required_reviews.append("Missing review_checklist.json; human sign-off has not been recorded.")
    else:
        try:
            checklist = _load_json(checklist_path)
            for field in REVIEW_FIELDS:
                if checklist.get(field) != "approved":
                    required_reviews.append(f"Human {field} review is not approved.")
            if checklist.get("human_review_status") != "approved":
                required_reviews.append("Human review status is not approved.")
        except ValueError as exc:
            errors.append(str(exc))

    if not (root / "review_checklist.md").is_file():
        required_reviews.append("Missing review_checklist.md.")
    if not (root / "preview" / "frames").is_dir() or not any((root / "preview" / "frames").glob("*")):
        required_reviews.append("Missing preview/frames; visual inspection evidence is absent.")

    if errors:
        status = "FAIL"
    elif required_reviews:
        status = "REVIEW_REQUIRED"
    else:
        status = "APPROVED_FOR_UPLOAD"

    return {"schema_version": 1, "status": status, "mode": mode, "topic": manifest.get("topic"),
            "errors": errors, "warnings": warnings, "required_human_reviews": sorted(set(required_reviews)),
            "technical_probe": probe, "qa_statuses": qa_results,
            "publication_eligible": status == "APPROVED_FOR_UPLOAD",
            "notice": "Technical validation is not editorial approval. APPROVED_FOR_UPLOAD requires explicit recorded human review and reviewed rights metadata."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", help="Directory containing the standardized review package")
    parser.add_argument("--output", default=None, help="Optional package_summary.json output path")
    args = parser.parse_args()
    result = validate_package(args.package_dir)
    output = Path(args.output) if args.output else Path(args.package_dir) / "package_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["status"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
