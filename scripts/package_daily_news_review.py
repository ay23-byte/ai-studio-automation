"""Package a rendered, script-reviewed daily-news Short for human review.

Translates the news renderer's output and QA reports into the shared review
package.  Rights stay unreviewed until a human opens the actual source terms.
This tool never uploads or publishes.

Exit codes: 0 package written and awaiting human review; 2 package written but
it fails automated QA (it cannot be approved); 1 the package could not be built.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import review_contract as contract
from core import review_packaging as packaging

CHECKLIST = (
    "Verify the story facts against the evidence packet and the original sources",
    "Check every scene visual against its narration; reject unrelated footage",
    "Listen to the full narration and audio mix",
    "Check caption wording, timing and safe-area placement",
    "Open each asset's source; confirm licence terms, attribution and commercial use",
    "Confirm the AI disclosure and platform policy requirements",
    "Confirm the presenter is genuinely animated and lip-synced, or that its absence is acceptable",
)


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _sub_report(root: Path, name: str, report, *, missing_blocks: bool, label: str) -> None:
    """Carry a renderer QA report into the package without upgrading it."""
    if not isinstance(report, dict):
        packaging.write_qa(root, name, "FAIL" if missing_blocks else "REVIEW",
                           [f"The {label} report was not produced; a human must check this in full."])
        return
    raw = str(report.get("status") or "NOT_RUN").upper()
    if raw in {"PASS", "REVIEW"}:
        status = "REVIEW"  # an automated pass is evidence for the reviewer, never the approval itself
    elif raw == "FAIL" or (raw == "ERROR" and missing_blocks):
        status = "FAIL"
    else:  # NOT_RUN / UNAVAILABLE / ERROR of an advisory check: never a pass
        status = "REVIEW"
    details = [f"Automated {label}: {raw}."]
    for key in ("reason", "summary"):
        if report.get(key):
            details.append(str(report[key])[:400])
    for item in (report.get("issues") or report.get("errors") or report.get("warnings") or [])[:8]:
        details.append(str(item.get("message") if isinstance(item, dict) else item)[:300])
    packaging.write_qa(root, name, status, details, automated_status=raw)


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
    if root in (render_dir, script_path.parent) or root in render_dir.parents:
        raise ValueError("output_dir must be a dedicated package directory.")
    if root.exists():
        shutil.rmtree(root)  # only ever the dedicated package directory
    (root / "qa").mkdir(parents=True)

    packaging.copy_into(video, root, "video.mp4")
    packaging.copy_into(captions, root, "captions.ass")
    packaging.copy_into(script_path, root, "evidence/approved_script.json")
    packaging.copy_into(render_dir / "production_manifest.json", root, "evidence/renderer_manifest.json")
    packaging.copy_into(qa_report_path, root, "evidence/technical_qa.json")
    for filename in ("licenses.csv", "credits.txt"):
        if (render_dir / filename).is_file():
            packaging.copy_into(render_dir / filename, root, f"evidence/{filename}")

    source_scenes = script.get("scenes") or []
    shot_rows = renderer_manifest.get("shots") or []
    if not source_scenes or len(source_scenes) != len(shot_rows):
        raise ValueError("Renderer shot count does not match the approved script scene count.")
    scenes = []
    for index, (scene, shot) in enumerate(zip(source_scenes, shot_rows), 1):
        asset = Path(str(scene.get("asset_path") or "")).resolve()
        sidecar = asset.with_suffix(asset.suffix + ".source.json")
        if not asset.is_file() or not sidecar.is_file():
            raise FileNotFoundError(f"Scene {index} source asset or provenance sidecar is missing.")
        relative = packaging.copy_into(asset, root, f"assets/scene_{index:02d}{asset.suffix.lower() or '.bin'}")
        provenance, rights = packaging.describe_asset(
            packaging.load_sidecar(asset), prompt=str(scene.get("image") or ""))
        scenes.append({
            "scene_id": str(shot.get("shot_id") or f"scene-{index:02d}"),
            "narration": str(shot.get("narration") or scene.get("say") or ""),
            "start_seconds": shot.get("start_seconds", shot.get("start")),
            "duration_seconds": shot.get("duration_seconds", shot.get("duration")),
            "source_ids": scene.get("source_ids") or [],
            "ai_disclosure": scene.get("ai_disclosure") is True,
            "assets": [{"path": relative, "asset_type": packaging.asset_type_of(asset),
                        "provenance": provenance, "rights": rights}],
        })

    extra_failures = []
    if str(qa.get("status") or "").upper() == "FAIL":
        extra_failures.append("The news technical QA FAILED: " + "; ".join(
            str(item)[:200] for item in (qa.get("errors") or [])[:6]))
    status, details = packaging.technical_report(root, "daily_news", root / "video.mp4", extra_failures)
    packaging.write_qa(root, "technical", status, details, news_qa_status=qa.get("status"))
    advanced = render_dir / "qa" / "advanced_qa"
    _sub_report(root, "semantic", _json(advanced / "semantic_qa_report.json"),
                missing_blocks=False, label="shot-to-narration screening")
    _sub_report(root, "audio", _json(advanced / "audio_quality_report.json"),
                missing_blocks=True, label="audio metering")
    _sub_report(root, "captions", _json(advanced / "caption_qa_report.json"),
                missing_blocks=False, label="caption audit")
    _sub_report(root, "assets", _json(advanced / "scene_asset_provenance_report.json"),
                missing_blocks=False, label="asset provenance audit")

    frames_dir = root / "preview" / "frames"
    frames = sorted((advanced / "sample_frames").glob("*.jpg")) if (advanced / "sample_frames").is_dir() else []
    if frames:
        frames_dir.mkdir(parents=True)
        for index, frame in enumerate(frames, 1):
            shutil.copy2(frame, frames_dir / f"frame_{index:03d}.jpg")
    else:
        contract.extract_preview_frames(root / "video.mp4", frames_dir, count=6)

    sources = []
    for number, item in enumerate(script.get("sources") or script.get("evidence") or [], 1):
        if isinstance(item, dict):
            sources.append({"source_id": str(item.get("source_id") or f"S{number}"),
                            "title": str(item.get("title") or "")[:300],
                            "publisher": str(item.get("publisher") or "")[:200],
                            "url": str(item.get("url") or ""), "tier": item.get("tier")})
    title = str(script.get("title") or script.get("topic") or "Daily news Short").strip()
    try:
        from core.publish import build_metadata
        meta = build_metadata({"title": title, "format": "news_short", "language": script.get("language"),
                               "category": "news"}, script, render_dir)
        publish = packaging.publish_metadata(meta["title"], meta["description"], meta["tags"])
        publish["category_id"] = meta.get("category_id")
    except Exception as exc:
        publish = packaging.publish_metadata(title, script.get("description") or "", script.get("tags") or [])
        publish["note"] = f"Derived from the script only ({type(exc).__name__})."
    narration = packaging.narration_metadata(render_dir)
    manifest = contract.build_manifest(
        root, mode="daily_news", topic=title, title=publish["title"] or title, scenes=scenes,
        research={"sources": sources, "claims": []}, narration=narration,
        script=packaging.script_metadata(script),
        extra={"publish": publish, "evidence_dir": "evidence",
               "presenter": renderer_manifest.get("presenter") or None})
    summary = contract.write_package(root, manifest, checklist_title="Daily news Short: human review required",
                                     checklist_items=CHECKLIST)
    print(f"Review package created: {root}")
    print(f"Status: {summary['status']}. No YouTube upload or publication was attempted.")
    for line in summary["errors"]:
        print(f"  BLOCKING: {line}")
    return root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", required=True)
    parser.add_argument("--render-dir", required=True)
    parser.add_argument("--qa-report", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        package = create_package(args.script, args.render_dir, args.qa_report, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"::error::Could not build the daily-news review package: {exc}")
        return 1
    status = json.loads((package / "package_summary.json").read_text(encoding="utf-8"))["status"]
    return 0 if status == "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
