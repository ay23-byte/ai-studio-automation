"""Assemble the shared review package for a pipeline-built Short.

Used for standard Shorts and for automatically built daily-news Shorts: both
are rendered by ``core.pipeline.build_shorts`` and leave the same build
directory.  The package is review-only; nothing here uploads.

    python scripts/package_standard_short_review.py \
        --build-dir build/job_7 --video output/shorts_7.mp4 --mode shortform

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

SHORT_MODES = ("shortform", "daily_news")
CHECKLIST = (
    "Watch the complete Short from beginning to end",
    "Check every visual against its narration; reject unrelated or misleading visuals",
    "Listen to the full narration and mix",
    "Read the captions against the audio (wording, timing, safe area)",
    "Open each original source page; confirm licence, attribution and commercial use",
    "Confirm the facts, and for news confirm every cited source",
    "Confirm the AI-content disclosure",
)


def _manifest_files(build: Path) -> dict[int, str]:
    """Scene number -> file name, as recorded by the pipeline's asset manifest."""
    rows = packaging.load_json(build / "asset_manifest.json").get("assets")
    table = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get("scene") or "").isdigit() and row.get("file"):
            table[int(row["scene"])] = Path(str(row["file"])).name  # a bare name: never a path out of the build
    return table


def _scene_media(build: Path, index: int, recorded: dict[int, str] | None = None) -> Path | None:
    """The visual the renderer actually used for this scene (0-based index)."""
    names = [(recorded or {}).get(index + 1)] if (recorded or {}).get(index + 1) else []
    names += [f"raw{index}.mp4", f"img{index}.jpg", f"img{index}.png", f"card{index}.jpg"]
    for name in names:
        candidate = build / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _timing(build: Path) -> list[dict]:
    try:
        rows = json.loads((build / "scene_timing.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return rows if isinstance(rows, list) else []


def _research(script: dict, build: Path, mode: str) -> dict:
    """Sources the reviewer must open.  News carries the corroboration record."""
    verification = packaging.load_json(build / "verification.json")
    rows = verification.get("sources") if isinstance(verification.get("sources"), list) else None
    if rows is None:
        rows = [item for item in (script.get("sources") or []) if isinstance(item, dict)]
    sources = []
    for number, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            continue
        sources.append({
            "source_id": str(item.get("source_id") or f"S{number}"),
            "title": str(item.get("title") or "")[:300], "publisher": str(item.get("publisher") or "")[:200],
            "url": str(item.get("url") or item.get("link") or ""), "tier": item.get("tier"),
            "published_at": item.get("published_at") or item.get("published"),
        })
    claims = []
    for number, item in enumerate(script.get("claims") or [], 1):
        if isinstance(item, dict) and str(item.get("text") or item.get("claim") or "").strip():
            claims.append({"claim_id": str(item.get("claim_id") or item.get("id") or f"C{number}"),
                           "text": str(item.get("text") or item.get("claim"))[:800],
                           "evidence": str(item.get("evidence") or "")[:800],
                           "source_ids": [str(value) for value in (item.get("source_ids") or [])]})
    return {"sources": sources, "claims": claims,
            "corroboration": {"status": verification.get("status"), "method": verification.get("method")}
            if verification else None}


def create_review_package(script: dict, build_dir, render_dir=None, *, video=None, mode: str = "shortform",
                          output_dir=None, pipeline_job_id=None, language: str | None = None) -> Path:
    """Build ``shared_review_package`` and return its path.

    ``render_dir`` (legacy call style) is a folder holding ``video.mp4`` and
    ``captions.ass``; alternatively pass ``video`` and the captions are taken
    from the build directory.
    """
    if mode not in SHORT_MODES:
        raise ValueError(f"mode must be one of {SHORT_MODES}; got {mode!r}.")
    build = Path(build_dir).resolve()
    if not build.is_dir():
        raise FileNotFoundError(f"Build directory does not exist: {build}")
    if render_dir is not None:
        render = Path(render_dir).resolve()
        video_path, captions = render / "video.mp4", render / "captions.ass"
        root = Path(output_dir).resolve() if output_dir else render / "shared_review_package"
    else:
        if video is None:
            raise ValueError("Pass either render_dir or video.")
        video_path, captions = Path(video).resolve(), build / "subs.ass"
        root = Path(output_dir).resolve() if output_dir else build / "shared_review_package"
    if not video_path.is_file():
        raise FileNotFoundError(f"The rendered Short is missing: {video_path}")
    if not captions.is_file() or captions.stat().st_size == 0:
        raise FileNotFoundError(f"The captions file is missing or empty: {captions}")
    scenes_in = script.get("scenes") if isinstance(script, dict) else None
    if not isinstance(scenes_in, list) or not scenes_in:
        raise ValueError("The script contains no scenes.")
    if root == build:
        raise ValueError("The package needs its own directory, not the build directory.")
    if root.exists():
        shutil.rmtree(root)  # only ever the dedicated package directory
    (root / "qa").mkdir(parents=True)

    packaging.copy_into(video_path, root, "video.mp4")
    packaging.copy_into(captions, root, "captions.ass")
    for name in ("credits.txt", "licenses.csv", "asset_manifest.json", "quality_report.json",
                 "audio_quality_report.json", "visual_fallback_report.json", "narration_synthesis_report.json",
                 "verification.json", "script.json"):
        if (build / name).is_file():
            packaging.copy_into(build / name, root, f"evidence/{name}")

    timing = _timing(build)
    recorded = _manifest_files(build)
    scenes, missing = [], []
    for index, scene in enumerate(scenes_in):
        scene = scene if isinstance(scene, dict) else {}
        media = _scene_media(build, index, recorded)
        assets = []
        if media is None:
            missing.append(index + 1)
        else:
            relative = packaging.copy_into(media, root, f"assets/scene_{index + 1:02d}{media.suffix.lower()}")
            provenance, rights = packaging.describe_asset(
                packaging.load_sidecar(media), prompt=str(scene.get("image") or scene.get("visual") or ""))
            assets.append({"path": relative, "asset_type": packaging.asset_type_of(media),
                           "provenance": provenance, "rights": rights})
        row = timing[index] if index < len(timing) and isinstance(timing[index], dict) else {}
        scenes.append({"scene_id": f"scene-{index + 1:02d}",
                       "narration": str(scene.get("say") or scene.get("narration") or ""),
                       "start_seconds": row.get("start"), "duration_seconds": row.get("duration"),
                       "assets": assets})
    if missing:
        raise FileNotFoundError("The rendered visual is missing for scene(s): "
                                + ", ".join(str(item) for item in missing))

    quality = packaging.load_json(build / "quality_report.json")
    quality_status = str(quality.get("status") or "").upper()
    extra_failures = []
    if not quality:
        extra_failures.append("The build produced no quality_report.json.")
    elif quality_status == "FAIL":
        extra_failures.append("The pipeline quality gate FAILED: " + "; ".join(
            str(item.get("message") if isinstance(item, dict) else item)[:200]
            for item in (quality.get("issues") or quality.get("errors") or [])[:6]))
    if quality.get("upload_blocked"):
        extra_failures.append("The pipeline quality gate blocked upload of this build: "
                              + str(quality.get("upload_block_reason") or "no reason recorded")[:400])
    status, details = packaging.technical_report(root, mode, root / "video.mp4", extra_failures)
    packaging.write_qa(root, "technical", status, details, pipeline_quality_status=quality_status or None)

    narration = packaging.narration_metadata(build)
    audio_state, audio_details = packaging.audio_status(build, narration)
    packaging.write_qa(root, "audio", audio_state, audio_details)
    packaging.write_qa(root, "semantic", "REVIEW",
                       ["No automated check establishes that a visual matches its narration.",
                        "A human must compare every shot with its narration."])
    packaging.write_qa(root, "captions", "REVIEW",
                       ["Captions were generated from the narration timing.",
                        "A human must check wording, timing, spelling and placement."])
    fallback = packaging.load_json(build / "visual_fallback_report.json")
    asset_details = ["A human must open each source, and confirm licence terms, attribution and usage rights."]
    asset_state = "REVIEW"
    if fallback:
        asset_details.append(f"Fallback text cards: {fallback.get('fallback_card_count')} of "
                             f"{fallback.get('visual_count')} visuals.")
        if str(fallback.get("status") or "").upper() == "FAIL":
            asset_state = "FAIL"
            asset_details.append("Too much of this Short is fallback cards; a card slideshow is not a "
                                 "publishable Short. Rebuild when visual providers are available.")
    packaging.write_qa(root, "assets", asset_state, asset_details)

    probe = contract.probe_video(root / "video.mp4")
    contract.extract_preview_frames(root / "video.mp4", root / "preview" / "frames",
                                    count=min(8, max(3, len(scenes))), duration_seconds=probe["duration_seconds"])

    title = str(script.get("title") or "Untitled Short").strip()
    row = {"title": title, "format": "news_short" if mode == "daily_news" else "shorts",
           "language": language or script.get("language"), "category": script.get("category")}
    try:
        from core.publish import build_metadata
        meta = build_metadata(row, script, build)
        publish = packaging.publish_metadata(meta["title"], meta["description"], meta["tags"])
        publish["category_id"] = meta.get("category_id")
    except Exception as exc:  # metadata is fixed at build time; say so if it could not be derived
        publish = packaging.publish_metadata(title, script.get("description") or "", script.get("tags") or [])
        publish["note"] = f"Derived from the script only ({type(exc).__name__})."
    manifest = contract.build_manifest(
        root, mode=mode, topic=title, title=publish["title"] or title, scenes=scenes,
        pipeline_job_id=pipeline_job_id, research=_research(script, build, mode),
        narration=narration, script=packaging.script_metadata(script),
        extra={"publish": publish, "evidence_dir": "evidence"})
    summary = contract.write_package(root, manifest, checklist_title="Short: human review required",
                                     checklist_items=CHECKLIST)
    print(f"Review package: {root}")
    print(f"Status: {summary['status']}. Nothing was uploaded.")
    for line in summary["errors"]:
        print(f"  BLOCKING: {line}")
    return root


def _job_id_from(build: Path):
    name = build.name
    return int(name[4:]) if name.startswith("job_") and name[4:].isdigit() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", required=True, help="build/job_<id> left by the pipeline")
    parser.add_argument("--video", required=True, help="The rendered MP4, e.g. output/shorts_<id>.mp4")
    parser.add_argument("--mode", choices=SHORT_MODES, default="shortform")
    parser.add_argument("--output-dir", default=None, help="Default: <build-dir>/shared_review_package")
    args = parser.parse_args(argv)
    build = Path(args.build_dir)
    try:
        script = json.loads((build / "script.json").read_text(encoding="utf-8"))
        package = create_review_package(script, build, video=args.video, mode=args.mode,
                                        output_dir=args.output_dir, pipeline_job_id=_job_id_from(build.resolve()))
    except (OSError, ValueError, contract.ContractError) as exc:
        print(f"::error::Could not build the review package: {exc}")
        return 1
    status = json.loads((package / "package_summary.json").read_text(encoding="utf-8"))["status"]
    return 0 if status == "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
