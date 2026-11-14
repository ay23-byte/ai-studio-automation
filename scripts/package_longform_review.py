"""Assemble the shared review package for a rendered long-form documentary.

Translates the documentary evidence dossier (visual review manifest, licence
register, research ledger, audio and quality reports) into the shared
manifest.  It changes nothing about rendering and uploads nothing.

    python scripts/package_longform_review.py \
        --build-dir build/job_3 --video output/longform_3.mp4

Exit codes: 0 package written and awaiting human review; 2 package written but
it fails automated QA (it cannot be approved); 1 the package could not be built.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import review_contract as contract
from core import review_packaging as packaging

CHECKLIST = (
    "Watch the complete documentary from beginning to end",
    "Verify every claim against its cited source in the research section",
    "Check every visual against its narration and historical context",
    "Review presenter lip-sync, gestures and presenter/B-roll balance",
    "Listen for narration clarity, clipping, silence and music balance",
    "Review subtitle wording, timing, spelling and safe zones",
    "Open every asset's source page; confirm licence, attribution and restrictions",
    "Confirm the AI-generated-content disclosure",
)
EVIDENCE_FILES = (
    "licenses.csv", "credits.txt", "visual_review_report.json", "visual_consistency_report.json",
    "audio_quality_report.json", "quality_report.json", "research_verification_report.json",
    "narration_synthesis_report.json", "release_metadata.json", "script.json", "chapter_timing.json",
    "provider_report.json", "provider_report.md",
)


def _license_rows(build: Path) -> dict[str, dict]:
    path = build / "licenses.csv"
    if not path.is_file():
        raise FileNotFoundError("The documentary build is missing licenses.csv.")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {str(row.get("shot_id") or ""): row for row in csv.DictReader(handle)}


def _technical_failures(build: Path) -> list[str]:
    """Automated gates that already blocked a documentary upload stay blocking."""
    failures = []
    quality = packaging.load_json(build / "quality_report.json")
    if not quality:
        failures.append("The build produced no quality_report.json.")
    elif str(quality.get("status") or "").upper() != "PASS":
        failures.append(f"The final video quality report is {quality.get('status')!r}, not PASS.")
    consistency = packaging.load_json(build / "visual_consistency_report.json")
    summary = consistency.get("summary") if isinstance(consistency.get("summary"), dict) else {}
    if not consistency:
        failures.append("The build produced no visual_consistency_report.json.")
    else:
        counts = {key: int(summary.get(key) or 0) for key in ("fail_count", "review_count", "unavailable_count")}
        if str(consistency.get("status") or "").upper() != "PASS" or any(counts.values()):
            shots = [f"{row.get('shot_id')}: {'; '.join(row.get('issues') or [])}"
                     for row in (consistency.get("shots") or []) if row.get("status") == "fail"][:8]
            failures.append(f"Visual consistency report is {consistency.get('status')!r} "
                            f"(fail {counts['fail_count']}, review {counts['review_count']}, "
                            f"unavailable {counts['unavailable_count']})"
                            + (": " + " | ".join(shots) if shots else "."))
    return failures


def _research(build: Path) -> tuple[dict, list[str]]:
    report = packaging.load_json(build / "research_verification_report.json")
    sources = [{"source_id": str(item.get("source_id")), "title": item.get("title"),
                "publisher": item.get("publisher"), "url": item.get("url"),
                "published_at": item.get("published_at"), "url_valid": item.get("url_valid"),
                "verification_state": item.get("verification_state")}
               for item in (report.get("sources") or []) if isinstance(item, dict) and item.get("source_id")]
    claims = [{"claim_id": str(item.get("claim_id")), "text": item.get("text"),
               "evidence": item.get("evidence"), "source_ids": item.get("valid_source_ids") or [],
               "traceability_status": item.get("traceability_status")}
              for item in (report.get("claims") or []) if isinstance(item, dict) and item.get("claim_id")]
    problems = []
    if not report:
        problems.append("The build produced no research_verification_report.json.")
    else:
        if not claims:
            problems.append("The script has no structured claim ledger; claims cannot be reviewed one by one.")
        if not sources:
            problems.append("No research sources were recorded.")
        for item in sources:
            if not item.get("url_valid") or not contract.is_http_url(item.get("url")):
                problems.append(f"Research source {item['source_id']} has no valid URL.")
        known = {item["source_id"] for item in sources}
        for item in claims:
            refs = set(item["source_ids"])
            if (item.get("traceability_status") != "source_linked_pending_human_review" or not refs
                    or not refs <= known or not str(item.get("evidence") or "").strip()):
                problems.append(f"Claim {item['claim_id']} has no traceable source reference and evidence.")
    return {"sources": sources, "claims": claims}, problems


def create_package(build_dir, video_file, output_dir=None, *, pipeline_job_id=None) -> Path:
    build = Path(build_dir).resolve()
    video = Path(video_file).resolve()
    if not build.is_dir():
        raise FileNotFoundError(f"Documentary build directory does not exist: {build}")
    if not video.is_file() or video.stat().st_size < 100_000:
        raise FileNotFoundError(f"Final documentary MP4 is missing or unexpectedly small: {video}")
    visual = packaging.load_json(build / "visual_review_report.json")
    shots = visual.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("visual_review_report.json must contain a non-empty shots array.")
    captions = build / "subs.ass"
    if not captions.is_file() or captions.stat().st_size == 0:
        raise FileNotFoundError("The documentary build is missing its non-empty subs.ass captions file.")
    licences = _license_rows(build)
    root = Path(output_dir).resolve() if output_dir else build / "shared_review_package"
    if root == build:
        raise ValueError("output_dir must be a dedicated directory, not the build root.")
    if root.exists():
        shutil.rmtree(root)  # only ever the dedicated package directory
    (root / "qa").mkdir(parents=True)
    frames_dir = root / "preview" / "frames"
    frames_dir.mkdir(parents=True)

    packaging.copy_into(video, root, "video.mp4")
    packaging.copy_into(captions, root, "captions.ass")
    for name in EVIDENCE_FILES:
        if (build / name).is_file():
            packaging.copy_into(build / name, root, f"evidence/{name}")

    scenes, preview_count, screening_counts, licence_blocks = [], 0, {}, []
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f"Visual review shot {index} is not an object.")
        shot_id = str(shot.get("shot_id") or f"shot_{index:04d}")
        raw_media = str(shot.get("media_path") or "").strip()
        media = Path(raw_media) if raw_media else None
        if media is not None and not media.is_absolute():
            # Dossier paths are relative to the repository or to the build directory.
            media = next((base / media for base in (Path.cwd(), build, build.parent.parent)
                          if (base / media).is_file()), media)
        if media is None or not media.is_file():
            raise FileNotFoundError(f"Documentary media for {shot_id} is missing: {raw_media!r}")
        relative = packaging.copy_into(media, root, f"assets/{shot_id}{media.suffix.lower() or '.bin'}")
        metadata = {**packaging.load_sidecar(media),
                    **(shot.get("source") if isinstance(shot.get("source"), dict) else {})}
        if shot.get("ai_generated"):
            metadata["ai_generated"] = True
        row = licences.get(shot_id, {})
        provenance, rights = packaging.describe_asset(
            metadata, prompt=str(shot.get("wan_generation_prompt") or shot.get("visual_prompt") or ""),
            licence_row=row)
        if row:
            rights["register_use_status"] = row.get("use_status")
            if (row.get("use_status") != "eligible_pending_human_review"
                    or str(row.get("fallback_required") or "").strip().lower() in {"true", "1", "yes"}):
                licence_blocks.append(f"{shot_id}: licence register status {row.get('use_status')!r}"
                                      + (", fallback required" if row.get("fallback_required") else ""))
        else:
            licence_blocks.append(f"{shot_id}: not present in the licence register.")
        screening = shot.get("semantic_screening") if isinstance(shot.get("semantic_screening"), dict) else {}
        state = str(screening.get("status") or "not_run").lower()
        screening_counts[state] = screening_counts.get(state, 0) + 1
        scenes.append({
            "scene_id": shot_id,
            "narration": str(shot.get("narration") or ""),
            "visual_brief": str(shot.get("visual_prompt") or "")[:600] or None,
            "start_seconds": shot.get("start_seconds"), "duration_seconds": shot.get("duration_seconds"),
            "screening": {"status": state, "reason": screening.get("reason"),
                          "match_score": screening.get("match_score"),
                          "recommendation": screening.get("recommendation")},
            "assets": [{"path": relative, "asset_type": str(shot.get("media_type") or packaging.asset_type_of(media)),
                        "provenance": provenance, "rights": rights}],
        })
        for number, frame in enumerate(shot.get("sample_frames") or [], 1):
            frame_path = Path(str(frame))
            if frame_path.is_file():
                shutil.copy2(frame_path, frames_dir / f"{shot_id}_{number:02d}{frame_path.suffix.lower() or '.jpg'}")
                preview_count += 1

    unresolved = [f"{item.get('shot', 'unknown')}: {item.get('message')}"
                  for item in (visual.get("issues") or []) if isinstance(item, dict)
                  and item.get("level") == "error"
                  and "AI visual-to-narration screening status" not in str(item.get("message") or "")]
    status, details = packaging.technical_report(
        root, "longform", root / "video.mp4",
        _technical_failures(build) + [f"Unresolved media error, {item}" for item in unresolved[:12]])
    packaging.write_qa(root, "technical", status, details)

    research, research_problems = _research(build)
    flagged = sum(count for state, count in screening_counts.items() if state != "match")
    packaging.write_qa(
        root, "semantic", "FAIL" if research_problems else "REVIEW",
        research_problems + [
            f"AI visual-to-narration screening: {json.dumps(screening_counts, sort_keys=True)}.",
            f"{flagged} shot(s) were not rated a match; each needs a written human decision.",
            "Screening is a model opinion, not approval. Source links are leads, not proof of a claim."],
        screening_counts=screening_counts)
    narration = packaging.narration_metadata(build)
    audio_state, audio_details = packaging.audio_status(build, narration)
    if not (build / "audio_quality_report.json").is_file():
        audio_state = "FAIL"
    packaging.write_qa(root, "audio", audio_state, audio_details)
    packaging.write_qa(root, "captions", "REVIEW",
                       ["A human must review subtitle wording, timing, readability and synchronisation."])
    credits = build / "credits.txt"
    if not credits.is_file() or credits.stat().st_size < 20:
        licence_blocks.append("credits.txt is missing or empty.")
    packaging.write_qa(
        root, "assets", "FAIL" if licence_blocks else "REVIEW",
        licence_blocks[:20] + ["The licence register is an eligibility assessment, not approval.",
                               "A human must open each original source and confirm the exact terms."])

    probe = contract.probe_video(root / "video.mp4")
    if preview_count == 0:
        contract.extract_preview_frames(root / "video.mp4", frames_dir, count=8,
                                        duration_seconds=probe["duration_seconds"])

    script = packaging.load_json(build / "script.json")
    release = packaging.load_json(build / "release_metadata.json")
    topic = str(release.get("title") or script.get("title") or script.get("topic") or video.stem)
    try:
        from core.publish import build_metadata
        meta = build_metadata({"title": topic, "format": "longform", "language": script.get("language"),
                               "category": script.get("category")}, script, build)
        publish = packaging.publish_metadata(meta["title"], meta["description"], meta["tags"])
        publish["category_id"] = meta.get("category_id")
    except Exception as exc:
        publish = packaging.publish_metadata(topic, release.get("description") or script.get("description") or "",
                                             release.get("tags") or script.get("tags") or [])
        publish["note"] = f"Derived from release_metadata.json only ({type(exc).__name__})."
    if credits.is_file():
        publish["credits_file"] = "evidence/credits.txt"
    manifest = contract.build_manifest(
        root, mode="longform", topic=topic, title=publish["title"] or topic, scenes=scenes,
        pipeline_job_id=pipeline_job_id, research=research, narration=narration,
        script=packaging.script_metadata(script), extra={"publish": publish, "evidence_dir": "evidence"})
    summary = contract.write_package(root, manifest, checklist_title="Documentary: human review required",
                                     checklist_items=CHECKLIST)
    print(f"Long-form review package: {root}")
    print(f"Status: {summary['status']}. Nothing was uploaded.")
    for line in summary["errors"]:
        print(f"  BLOCKING: {line}")
    return root


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)
    build = Path(args.build_dir).resolve()
    job = int(build.name[4:]) if build.name.startswith("job_") and build.name[4:].isdigit() else None
    try:
        package = create_package(build, args.video, args.output_dir, pipeline_job_id=job)
    except (OSError, ValueError) as exc:
        print(f"::error::Could not build the documentary review package: {exc}")
        return 1
    status = json.loads((package / "package_summary.json").read_text(encoding="utf-8"))["status"]
    return 0 if status == "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
