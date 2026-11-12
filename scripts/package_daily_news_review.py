"""Package a rendered daily-news Short for explicit human review.

Rights remain unknown until a human reviews the actual source terms. This tool
never uploads or publishes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _status(report, fallback="REVIEW"):
    if not isinstance(report, dict):
        return fallback
    value = str(report.get("status") or fallback).upper()
    return value if value in {"PASS", "REVIEW", "FAIL", "NOT_RUN", "ERROR", "UNAVAILABLE"} else fallback


def create_package(script_path: str | Path, render_dir: str | Path,
                   qa_report_path: str | Path, output_dir: str | Path) -> Path:
    script_path, render_dir = Path(script_path).resolve(), Path(render_dir).resolve()
    qa_report_path, root = Path(qa_report_path).resolve(), Path(output_dir).resolve()
    script = _json(script_path)
    if not isinstance(script, dict):
        raise ValueError("Approved script JSON is missing or invalid.")
    video = render_dir / "review_short.mp4"
    captions = render_dir / "captions.ass"
    renderer_manifest = _json(render_dir / "production_manifest.json")
    qa = _json(qa_report_path)
    if not video.is_file() or not captions.is_file() or not isinstance(renderer_manifest, dict):
        raise FileNotFoundError("Renderer must produce review_short.mp4, captions.ass and production_manifest.json.")
    if not isinstance(qa, dict):
        raise ValueError("Technical QA report is missing or invalid.")
    if root.exists():
        shutil.rmtree(root)
    (root / "assets").mkdir(parents=True)
    (root / "qa").mkdir(parents=True)
    (root / "preview" / "frames").mkdir(parents=True)

    shutil.copy2(video, root / "video.mp4")
    shutil.copy2(captions, root / "captions.ass")
    for filename, target in (("licenses.csv", "licenses.csv"), ("credits.txt", "credits.txt"),
                             ("voice.wav", "voice.wav")):
        source = render_dir / filename
        if source.is_file():
            shutil.copy2(source, root / target)

    source_scenes = script.get("scenes") or []
    shot_rows = renderer_manifest.get("shots") or []
    if len(source_scenes) != len(shot_rows):
        raise ValueError("Renderer shot count does not match the approved script scene count.")
    manifest_scenes = []
    for index, (scene, shot) in enumerate(zip(source_scenes, shot_rows), 1):
        asset = Path(str(scene.get("asset_path") or "")).resolve()
        sidecar = asset.with_suffix(asset.suffix + ".source.json")
        if not asset.is_file() or not sidecar.is_file():
            raise FileNotFoundError(f"Scene {index} source asset or provenance sidecar is missing.")
        metadata = _json(sidecar, {})
        if not isinstance(metadata, dict):
            metadata = {}
        suffix = asset.suffix.lower() or ".bin"
        relative = Path("assets") / f"scene_{index:02d}{suffix}"
        shutil.copy2(asset, root / relative)
        shutil.copy2(sidecar, root / "assets" / f"scene_{index:02d}{suffix}.source.json")

        provider = str(metadata.get("provider") or "").strip()
        source_url = str(metadata.get("page_url") or metadata.get("source_url") or metadata.get("url") or "").strip()
        asset_type = str(metadata.get("asset_type") or scene.get("asset_type") or "unknown")
        is_ai = "ai_generated" in asset_type.lower() or metadata.get("ai_generated") is True
        is_project_graphic = provider == "LabLens/AI Studio local graphic renderer"
        if is_ai:
            provenance = {"origin": "ai_generated", "provider": provider or None,
                          "prompt": str(scene.get("image") or scene.get("say") or "Prompt not retained")}
        elif provider and source_url:
            provenance = {"origin": "source", "provider": provider, "source_url": source_url}
        else:
            provenance = {"origin": "unknown", "provider": provider or None,
                          "source_url": source_url or None}
        license_name = metadata.get("licence") or metadata.get("license")
        manifest_scenes.append({
            "scene_id": str(shot.get("shot_id") or f"scene-{index:02d}"),
            "narration": str(shot.get("narration") or scene.get("say") or ""),
            "assets": [{
                "path": relative.as_posix(),
                "asset_type": str(scene.get("asset_type") or asset_type),
                "provenance": provenance,
                "rights": {"status": "unknown", "license": license_name or None,
                           "attribution": metadata.get("attribution_text")},
            }],
            "source_ids": scene.get("source_ids") or [],
            "ai_disclosure": scene.get("ai_disclosure") is True,
            "project_graphic": is_project_graphic,
        })

    _write(root / "production_manifest.json", {
        "schema_version": 1, "mode": "daily_news",
        "topic": str(script.get("title") or script.get("topic") or "Daily news Short"),
        "video_file": "video.mp4", "captions_file": "captions.ass",
        "scenes": manifest_scenes,
        "renderer_manifest": "renderer_manifest.json",
    })
    shutil.copy2(render_dir / "production_manifest.json", root / "renderer_manifest.json")

    advanced = render_dir / "advanced_qa"
    mapping = {
        "semantic": advanced / "semantic_qa_report.json",
        "audio": advanced / "audio_quality_report.json",
        "captions": advanced / "caption_qa_report.json",
        "assets": advanced / "scene_asset_provenance_report.json",
    }
    _write(root / "qa" / "technical.json", qa)
    for name, path in mapping.items():
        report = _json(path)
        if report is None:
            report = {"status": "NOT_RUN", "reason": f"{path.name} was not produced."}
        _write(root / "qa" / f"{name}.json", report)

    frames = sorted((advanced / "sample_frames").glob("*.jpg")) if (advanced / "sample_frames").is_dir() else []
    if not frames:
        result = subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", "1", "-i", str(video), "-frames:v", "1",
            str(root / "preview" / "frames" / "frame_001.jpg")
        ], capture_output=True, text=True, check=False, timeout=120)
        if result.returncode:
            raise RuntimeError("Could not extract a review preview frame.")
    else:
        for index, frame in enumerate(frames, 1):
            shutil.copy2(frame, root / "preview" / "frames" / f"frame_{index:03d}.jpg")

    checklist = {key: "pending" for key in
                 ("editorial", "factual", "visual", "audio", "rights", "disclosure")}
    checklist["human_review_status"] = "pending"
    _write(root / "review_checklist.json", checklist)
    (root / "review_checklist.md").write_text(
        "# Daily news Short — human review required\n\n"
        "- [ ] Verify story facts against the evidence packet and original sources\n"
        "- [ ] Check every scene visual against its narration; replace unrelated footage\n"
        "- [ ] Listen to the full narration and audio mix\n"
        "- [ ] Check English/Hindi caption wording, timing and safe-area placement\n"
        "- [ ] Verify each asset's actual licence terms, attribution and commercial-use rights\n"
        "- [ ] Confirm AI disclosure and platform policy requirements\n"
        "- [ ] Confirm presenter requirement/absence is acceptable for this release\n\n"
        "**This package is review-only. No YouTube upload or publication was attempted.**\n",
        encoding="utf-8",
    )
    summary = subprocess.run([
        "python", "scripts/validate_review_package.py", str(root),
        "--output", str(root / "package_summary.json")
    ], capture_output=True, text=True, check=False, timeout=120)
    if summary.returncode not in (0, 1):
        raise RuntimeError("Shared package validator failed to execute: " + summary.stderr[-1200:])
    result = _json(root / "package_summary.json", {})
    if result.get("status") != "REVIEW_REQUIRED":
        raise RuntimeError("New daily-news render must remain REVIEW_REQUIRED; validator returned "
                           + str(result.get("status")) + ": " + "; ".join(result.get("errors", [])))
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True)
    parser.add_argument("--render-dir", required=True)
    parser.add_argument("--qa-report", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    package = create_package(args.script, args.render_dir, args.qa_report, args.output_dir)
    print(f"Review package created: {package}")
    print("Status: REVIEW_REQUIRED. No YouTube upload or publication was attempted.")


if __name__ == "__main__":
    main()
