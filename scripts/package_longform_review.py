"""Assemble a shared, review-only package for a rendered long-form documentary.

This adapter consumes the existing documentary evidence dossier. It does not
change rendering, editorial decisions, rights status, or YouTube upload behavior.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path


def _read_json(path: Path, *, required: bool = True) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if required:
            raise ValueError(f"Cannot read required JSON {path}: {exc}") from exc
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _status(report: dict, fallback: str = "REVIEW") -> str:
    value = str(report.get("status") or fallback).upper()
    return value if value in {"PASS", "REVIEW", "FAIL"} else fallback


def _probe(video: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if result.returncode:
        raise ValueError("ffprobe could not verify the documentary MP4: " + result.stderr[-1000:])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON for the documentary MP4.") from exc
    streams = data.get("streams") or []
    videos = [row for row in streams if row.get("codec_type") == "video"]
    audios = [row for row in streams if row.get("codec_type") == "audio"]
    if len(videos) != 1 or not audios:
        raise ValueError("Documentary MP4 must contain exactly one video stream and at least one audio stream.")
    video_stream = videos[0]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise ValueError("Documentary MP4 has no valid positive duration.")
    return {
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "duration_seconds": round(duration, 3),
        "video_codec": video_stream.get("codec_name"),
        "audio_codecs": sorted({row.get("codec_name", "unknown") for row in audios}),
    }


def _license_rows(build: Path) -> dict[str, dict]:
    path = build / "licenses.csv"
    if not path.is_file():
        raise FileNotFoundError("Documentary build is missing licenses.csv.")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("shot_id") or ""): row for row in csv.DictReader(handle)}


def create_package(build_dir: str | Path, video_file: str | Path,
                   output_dir: str | Path | None = None) -> Path:
    build = Path(build_dir).resolve()
    video = Path(video_file).resolve()
    if not build.is_dir():
        raise FileNotFoundError(f"Documentary build directory does not exist: {build}")
    if not video.is_file() or video.stat().st_size < 100_000:
        raise FileNotFoundError(f"Final documentary MP4 is missing or unexpectedly small: {video}")

    visual = _read_json(build / "visual_review_report.json")
    shots = visual.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("visual_review_report.json must contain a non-empty shots array.")
    licenses = _license_rows(build)
    root = Path(output_dir).resolve() if output_dir else build / "shared_review_package"
    if root == build or build in root.parents:
        # Package may live under the build directory, but never overwrite the
        # build root itself or place package output inside an asset source folder.
        if root == build:
            raise ValueError("output_dir must be a dedicated child or sibling directory, not the build root.")
    assets_dir = root / "assets"
    frames_dir = root / "preview" / "frames"
    qa_dir = root / "qa"
    assets_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(video, root / "video.mp4")
    captions_source = build / "subs.ass"
    if not captions_source.is_file() or captions_source.stat().st_size == 0:
        raise FileNotFoundError("Documentary build is missing its non-empty subs.ass captions file.")
    shutil.copy2(captions_source, root / "captions.ass")
    for filename in ("licenses.csv", "credits.txt"):
        source = build / filename
        if source.is_file():
            shutil.copy2(source, root / filename)

    scenes = []
    preview_count = 0
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f"Visual review shot {index} is not an object.")
        shot_id = str(shot.get("shot_id") or f"shot_{index:04d}")
        raw_media = str(shot.get("media_path") or "").strip()
        media_path = Path(raw_media).resolve() if raw_media else None
        if not media_path or not media_path.is_file():
            raise FileNotFoundError(f"Documentary media for {shot_id} is missing: {raw_media!r}")
        suffix = media_path.suffix.lower() or ".bin"
        relative_asset = Path("assets") / f"{shot_id}{suffix}"
        shutil.copy2(media_path, root / relative_asset)

        source = shot.get("source") if isinstance(shot.get("source"), dict) else {}
        row = licenses.get(shot_id, {})
        asset_type = str(shot.get("media_type") or "unknown")
        source_url = str(source.get("page_url") or source.get("source_url") or source.get("url") or row.get("url") or "")
        provider = str(source.get("provider") or row.get("source") or "")
        is_ai = bool(shot.get("ai_generated")) or "ai_generated" in str(source.get("asset_type") or "").lower()
        if is_ai:
            provenance = {
                "origin": "ai_generated",
                "provider": provider or "unknown",
                "prompt": str(shot.get("visual_prompt") or shot.get("wan_generation_prompt") or "Prompt not retained."),
            }
        elif source_url:
            provenance = {"origin": "source", "provider": provider or "unknown", "source_url": source_url}
        else:
            provenance = {"origin": "unknown", "provider": provider or None, "source_url": None}

        scene_asset = {
            "path": relative_asset.as_posix(),
            "asset_type": asset_type,
            "provenance": provenance,
            # The documentary rights register is an eligibility assessment,
            # not the human sign-off required by the shared package contract.
            "rights": {
                "status": "unknown",
                "license": str(row.get("licence") or "") or None,
                "attribution": str(row.get("attribution_text") or "") or None,
            },
        }
        scenes.append({
            "scene_id": shot_id,
            "narration": str(shot.get("narration") or shot.get("visual_prompt") or shot_id),
            "start_seconds": shot.get("start_seconds"),
            "duration_seconds": shot.get("duration_seconds"),
            "assets": [scene_asset],
        })

        for frame_index, frame in enumerate(shot.get("sample_frames") or [], 1):
            frame_path = Path(str(frame)).resolve()
            if frame_path.is_file():
                target = frames_dir / f"{shot_id}_{frame_index:02d}{frame_path.suffix.lower() or '.jpg'}"
                if frame_path != target.resolve():
                    shutil.copy2(frame_path, target)
                preview_count += 1
        if preview_count == 0:
            # The final video frame is a fallback preview only; it does not
            # certify that the source asset is relevant or authentic.
            pass

    probe = _probe(video)
    technical_status = "PASS" if probe["width"] >= 1280 and probe["height"] >= 720 else "FAIL"
    _write_json(qa_dir / "technical.json", {
        "status": technical_status,
        "details": ["FFprobe verified video/audio streams and positive duration.", probe],
    })

    # Keep semantic screening separate from technical image-quality metrics.
    # A mismatch/error is important evidence for the human reviewer, but this
    # package is deliberately review-only: it must be assembled as REVIEW_REQUIRED
    # rather than misreporting a technical consistency FAIL as semantic FAIL.
    screening_counts = {
        status: sum(
            1 for shot in shots
            if str((shot.get("semantic_screening") or {}).get("status") or "not_run").lower() == status
        )
        for status in ("match", "mismatch", "uncertain", "unavailable", "error", "skipped", "not_run")
    }
    semantic_review = {
        "status": "REVIEW",
        "source_report": "visual_review_report.json",
        "semantic_screening_enabled": bool(visual.get("semantic_screening_enabled")),
        "screening_counts": screening_counts,
        "shots": [
            {
                "shot_id": str(shot.get("shot_id") or f"shot_{index + 1:04d}"),
                "status": str((shot.get("semantic_screening") or {}).get("status") or "not_run").lower(),
                "match_score": (shot.get("semantic_screening") or {}).get("match_score"),
                "recommendation": (shot.get("semantic_screening") or {}).get("recommendation"),
                "reason": (shot.get("semantic_screening") or {}).get("reason"),
            }
            for index, shot in enumerate(shots)
        ],
        "details": [
            "Automated visual-to-narration screening is advisory evidence, not human approval.",
            "Every mismatch, uncertain, error or not-run result must be inspected and resolved by a human reviewer before publication.",
        ],
    }
    _write_json(qa_dir / "semantic.json", semantic_review)

    visual_quality = _read_json(build / "visual_consistency_report.json", required=False)
    visual_quality_status = _status(visual_quality, "REVIEW")
    _write_json(qa_dir / "visual_quality.json", {
        "status": "REVIEW",
        "automated_status": visual_quality_status,
        "source_report": "visual_consistency_report.json" if visual_quality else None,
        "summary": visual_quality.get("summary") or {},
        "issues": visual_quality.get("issues") or [],
        "details": [
            "Resolution, exposure, contrast, saturation and sharpness diagnostics are technical quality signals, not semantic relevance.",
            "Any automated FAIL must be corrected or explicitly assessed during human visual review; it is not silently converted into PASS.",
        ],
    })

    report_map = {
        "audio": ("audio_quality_report.json",
                  "Human listening review remains required even when automated audio metering passes."),
        "captions": ("quality_report.json",
                     "Human review of subtitle wording, timing, readability and synchronization is required."),
    }
    for name, (filename, detail) in report_map.items():
        report = _read_json(build / filename, required=False)
        status = _status(report, "REVIEW")
        _write_json(qa_dir / f"{name}.json", {
            "status": "FAIL" if status == "FAIL" else "REVIEW",
            "source_report": filename if report else None,
            "details": [detail, report.get("summary") or report.get("details") or report.get("status")],
        })
    _write_json(qa_dir / "assets.json", {
        "status": "REVIEW",
        "details": [
            "Human review must inspect each original asset, source page, exact licence terms, attribution and restrictions.",
            "See licenses.csv and production_manifest.json; rights are intentionally not pre-approved.",
        ],
    })

    script = _read_json(build / "script.json", required=False)
    topic = str(script.get("title") or script.get("topic") or video.stem)
    _write_json(root / "production_manifest.json", {
        "schema_version": 1,
        "mode": "longform",
        "topic": topic,
        "video_file": "video.mp4",
        "captions_file": "captions.ass",
        "duration_seconds": probe["duration_seconds"],
        "width": probe["width"],
        "height": probe["height"],
        "scenes": scenes,
    })
    checklist = {field: "pending" for field in
                 ("editorial", "factual", "visual", "audio", "rights", "disclosure")}
    checklist["human_review_status"] = "pending"
    _write_json(root / "review_checklist.json", checklist)
    (root / "review_checklist.md").write_text(
        "# Documentary human review required\n\n"
        "- [ ] Watch the complete documentary from beginning to end\n"
        "- [ ] Verify factual claims against the research dossier\n"
        "- [ ] Check every visual against its narration and historical context\n"
        "- [ ] Review presenter lip-sync, gestures and presenter/B-roll balance\n"
        "- [ ] Listen for narration clarity, clipping, silence and music balance\n"
        "- [ ] Review subtitle wording, timing, spelling and safe zones\n"
        "- [ ] Inspect every asset's source, licence, attribution and restrictions\n"
        "- [ ] Confirm AI-generated-content disclosure and platform policy\n",
        encoding="utf-8",
    )

    if preview_count == 0:
        preview = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(video),
             "-frames:v", "1", str(frames_dir / "frame_001.jpg")],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if preview.returncode != 0 or not (frames_dir / "frame_001.jpg").is_file():
            raise RuntimeError("Could not create a preview frame for human review.")

    summary = subprocess.run(
        ["python", "scripts/validate_review_package.py", str(root),
         "--output", str(root / "package_summary.json")],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if summary.returncode not in (0, 1):
        raise RuntimeError("Shared review-package validator could not execute: " + summary.stderr[-1200:])
    result = _read_json(root / "package_summary.json")
    if result.get("status") != "REVIEW_REQUIRED":
        raise RuntimeError(
            "A new documentary package must remain REVIEW_REQUIRED; validator returned "
            + str(result.get("status")) + ": " + "; ".join(result.get("errors") or [])
        )
    from scripts.build_review_dashboard import build_dashboard
    build_dashboard(root)
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    package = create_package(args.build_dir, args.video, args.output_dir)
    print(f"Long-form shared review package: {package}")
    print("Status: REVIEW_REQUIRED (human editorial, factual, visual, audio, rights and disclosure review remains mandatory).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
