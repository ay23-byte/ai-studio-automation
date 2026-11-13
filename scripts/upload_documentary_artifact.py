"""Run automated documentary release checks and upload as Private only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# When invoked as ``python scripts/upload_documentary_artifact.py``, Python puts
# the scripts/ directory (not the repository root) on sys.path. Add the root so
# the sibling core/ package is importable in GitHub Actions and local CLI use.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.uploader import find_private_video_by_marker, upload, wait_processed


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return value


def _find_release_metadata(root: Path) -> tuple[Path, dict]:
    matches = sorted(root.glob("build/job_*/release_metadata.json"))
    if len(matches) != 1:
        raise ValueError(
            "Expected exactly one build/job_*/release_metadata.json in the render artifact; "
            f"found {len(matches)}. Refusing to guess release metadata."
        )
    path = matches[0]
    data = _load_json(path)
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title or not description:
        raise ValueError("Release metadata must contain a non-empty title and description.")
    tags = data.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("Release metadata tags must be a list of strings.")
    return path, {"title": title, "description": description, "tags": tags[:30]}


def _find_video(root: Path, build_dir: Path) -> tuple[Path, dict]:
    """Find and independently probe the single documentary MP4 in the render artifact.

    The make-longform producer packages output/*.mp4 and build/job_*/. It does
    not create build/live_documentary_validation/mp4-probe-report.json, so the
    uploader must not depend on that unrelated validation workflow's report.
    """
    # The producer pairs build/job_<id>/ with output/longform_<id>.mp4.
    # output/ also contains previews and other MP4 products; never glob them.
    build_name = build_dir.name
    if not build_name.startswith("job_") or not build_name[4:].isdigit():
        raise ValueError(f"Cannot derive a documentary job ID from build directory: {build_name}")
    job_id = build_name[4:]
    output_dir = root / "output"
    video_path = output_dir / f"longform_{job_id}.mp4"
    if not video_path.is_file() or video_path.stat().st_size <= 100_000:
        raise ValueError(
            f"Expected final export output/longform_{job_id}.mp4 for {build_name}; "
            "file is missing or too small. Other MP4s are not upload candidates."
        )
    try:
        # Reuse the same FFprobe-based delivery-profile gate used by release
        # certification. This verifies real audio/video streams, duration,
        # frame rate, landscape dimensions and stereo audio before upload.
        from core.release_certification import probe_release_video
        probe = probe_release_video(video_path)
    except Exception as exc:
        raise ValueError(f"Documentary MP4 failed independent FFprobe validation: {exc}") from exc
    return video_path, probe


def _validate_release_evidence(build_dir: Path) -> dict:
    """Require the generated documentary QA dossier before any upload attempt."""
    required = [
        "visual_review_report.json", "visual_consistency_report.json",
        "audio_quality_report.json", "research_verification_report.json",
        "research_review_decisions.json", "quality_report.json", "licenses.csv",
    ]
    reports = {}
    missing = [name for name in required if not (build_dir / name).is_file()]
    if missing:
        raise ValueError("Release evidence is incomplete; missing: " + ", ".join(missing))
    for name in required[:-1]:
        reports[name] = _load_json(build_dir / name)
    visual = reports["visual_review_report.json"]
    if not isinstance(visual.get("shots"), list) or not visual["shots"]:
        raise ValueError("Visual review report contains no shot manifest.")
    # Validate the persistent human decision store as the publication authority.
    # The raw manifest also contains AI semantic-screening triage flags; those
    # are not silently promoted to a hard media failure when a reviewer has
    # inspected the shot and explicitly approved it with substantive notes.
    from core.editorial_review import assert_visual_review_ready
    try:
        editorial_gate = assert_visual_review_ready(build_dir)
    except Exception as exc:
        raise ValueError(f"Visual/editorial review is not approved for release: {exc}") from exc
    unresolved_media = [
        item for item in visual.get("issues", [])
        if item.get("level") == "error"
        and "AI visual-to-narration screening status" not in str(item.get("message") or "")
    ]
    if unresolved_media:
        details = "; ".join(
            f"{item.get('shot', 'unknown')}: {item.get('message', 'unresolved media issue')}"
            for item in unresolved_media[:12]
        )
        raise ValueError("Visual review report contains unresolved media errors: " + details)
    consistency = reports["visual_consistency_report.json"]
    summary = consistency.get("summary") or {}
    consistency_status = str(consistency.get("status") or "MISSING").upper()
    fail_count = int(summary.get("fail_count") or 0)
    if consistency_status != "PASS" or fail_count or int(summary.get("review_count") or 0) or int(summary.get("unavailable_count") or 0):
        failed_shots = [
            f"{row.get('shot_id', 'unknown')}: {'; '.join(row.get('issues') or [])}"
            for row in consistency.get("shots") or []
            if row.get("status") == "fail"
        ]
        detail = (
            f"status={consistency_status}, fail_count={fail_count}, "
            f"review_count={int(summary.get('review_count') or 0)}, "
            f"unavailable_count={int(summary.get('unavailable_count') or 0)}"
        )
        if failed_shots:
            detail += "; failed shots: " + " | ".join(failed_shots[:12])
        raise ValueError("Visual consistency report blocks upload (" + detail + ").")
    audio = reports["audio_quality_report.json"]
    if audio.get("status") != "PASS":
        raise ValueError("Audio quality report is failed or incomplete.")
    quality = reports["quality_report.json"]
    if quality.get("status") != "PASS":
        raise ValueError("Final video quality report is failed or incomplete.")
    research = reports["research_verification_report.json"]
    sources = research.get("sources") or []
    claims = research.get("claims") or []
    source_ids = {str(item.get("source_id") or "") for item in sources}
    if not claims or not sources or len(claims) != int(research.get("claim_count") or 0) or len(sources) != int(research.get("source_count") or 0):
        raise ValueError("Research dossier must contain complete structured claim and source records.")
    from urllib.parse import urlparse
    for source in sources:
        parsed = urlparse(str(source.get("url") or ""))
        if not source.get("url_valid") or not source.get("metadata_complete") or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Research source is missing a valid URL, title or publisher: " + str(source.get("source_id")))
    for claim in claims:
        refs = {str(value) for value in (claim.get("valid_source_ids") or [])}
        if claim.get("traceability_status") != "source_linked_pending_human_review" or not refs or not refs.issubset(source_ids) or not str(claim.get("evidence") or "").strip():
            raise ValueError("Research claim lacks traceable source references/evidence: " + str(claim.get("claim_id")))
    import csv
    with (build_dir / "licenses.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    shot_ids = {str(item.get("shot_id") or "") for item in visual["shots"]}
    row_ids = [str(row.get("shot_id") or "") for row in rows]
    if len(rows) != len(visual["shots"]) or set(row_ids) != shot_ids or len(set(row_ids)) != len(row_ids):
        raise ValueError("Asset licence register must cover every shot exactly once.")
    blocked = [row.get("shot_id", "unknown") for row in rows if row.get("use_status") != "eligible_pending_human_review" or str(row.get("fallback_required") or "").lower() in {"true", "1", "yes"}]
    if blocked:
        raise ValueError("Asset licence/terms checks did not clear these assets: " + ", ".join(blocked[:12]))
    for row in rows:
        if not str(row.get("licence") or "").strip() or not str(row.get("attribution_text") or "").strip():
            raise ValueError("Asset is missing licence label or attribution: " + str(row.get("shot_id")))
    credits = build_dir / "credits.txt"
    if not credits.is_file() or credits.stat().st_size < 20:
        raise ValueError("Documentary credits.txt is missing or empty.")
    return {"shot_count": len(visual["shots"]), "licence_rows": len(rows),
            "research_claim_count": len(claims), "research_source_count": len(sources),
            "automated_fact_check_scope": "source traceability and evidence completeness only; not independent truth verification",
            "automated_rights_check_scope": "recognized licence metadata, per-shot attribution and credits completeness; not legal advice"}


def upload_automated_artifact(artifact_dir: Path, source_run_id: str) -> dict:
    if not source_run_id.isdigit():
        raise ValueError("Source workflow run ID must be numeric.")
    root = artifact_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Artifact directory does not exist: {root}")

    metadata_path, metadata = _find_release_metadata(root)
    video_path, probe = _find_video(root, metadata_path.parent)
    evidence = _validate_release_evidence(metadata_path.parent)
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()

    # A stable source-run marker makes retries idempotent. Search errors fail closed.
    marker = f"AI_STUDIO_SOURCE_RUN_ID:{source_run_id}"
    existing_video_id = find_private_video_by_marker(marker)
    if existing_video_id:
        video_id = existing_video_id
        print(f"Found existing private upload for source run {source_run_id}; skipping duplicate upload.")
    else:
        # Hard-code privacy and disable scheduling: this workflow must never publish.
        description = (metadata["description"].rstrip() + "\n\n" + marker).strip()
        video_id = upload(
            video_path,
            metadata["title"],
            description,
            metadata["tags"],
            privacy_status="private",
            disable_scheduling=True,
        )
    processing = wait_processed(video_id, max_wait=600)
    result = {
        "source_run_id": source_run_id,
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "privacy_status": "private",
        "processing_status": processing,
        "title": metadata["title"],
        "file": str(video_path.relative_to(root)),
        "size_bytes": video_path.stat().st_size,
        "sha256": digest,
        "duration_seconds": probe.get("duration_seconds"),
        "release_evidence": evidence,
    }
    output = root / "youtube-upload-result.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if processing != "ok":
        raise RuntimeError(f"YouTube processing did not complete successfully: {processing}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    args = parser.parse_args()
    failure_path = args.artifact_dir / "youtube-upload-failure.json"
    try:
        upload_automated_artifact(args.artifact_dir, args.source_run_id)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # Leave a machine-readable reason for the job summary and the failure alert.
        try:
            failure_path.write_text(json.dumps(
                {"source_run_id": args.source_run_id, "error_type": type(exc).__name__,
                 "error": f"{type(exc).__name__}: {exc}"[:1000]}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
        except OSError:
            pass
        return 1
    try:
        failure_path.unlink()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
