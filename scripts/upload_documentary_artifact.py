"""Upload one human-reviewed documentary artifact to YouTube as Private only."""
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


def _find_video(root: Path) -> tuple[Path, dict]:
    """Find and independently probe the single documentary MP4 in the render artifact.

    The make-longform producer packages output/*.mp4 and build/job_*/. It does
    not create build/live_documentary_validation/mp4-probe-report.json, so the
    uploader must not depend on that unrelated validation workflow's report.
    """
    # The artifact intentionally includes many chapter/shot clips under
    # build/job_*/. The finished export is written separately to output/*.mp4.
    # Never count intermediate clips as candidate uploads.
    output_dir = root / "output"
    candidates = sorted(
        path for path in output_dir.glob("*.mp4")
        if path.is_file() and path.stat().st_size > 100_000
    ) if output_dir.is_dir() else []
    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one final documentary MP4 under output/; "
            f"found {len(candidates)}. Intermediate build/job_*/ clips are ignored."
        )

    video_path = candidates[0]
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
    if any(item.get("level") == "error" for item in visual.get("issues", [])):
        raise ValueError("Visual review report contains unresolved media errors.")
    consistency = reports["visual_consistency_report.json"]
    summary = consistency.get("summary") or {}
    if consistency.get("status") not in {"PASS", "REVIEW"} or int(summary.get("fail_count") or 0):
        raise ValueError("Visual consistency report is failed or incomplete.")
    audio = reports["audio_quality_report.json"]
    if audio.get("status") not in {"PASS", "REVIEW"}:
        raise ValueError("Audio quality report is failed or incomplete.")
    quality = reports["quality_report.json"]
    if quality.get("status") not in {"PASS", "REVIEW"}:
        raise ValueError("Final video quality report is failed or incomplete.")
    research = reports["research_verification_report.json"]
    if int(research.get("claim_count") or 0) < 1 or int(research.get("source_count") or 0) < 1:
        raise ValueError("Research dossier must contain at least one traceable claim and source.")
    import csv
    with (build_dir / "licenses.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(visual["shots"]):
        raise ValueError("Asset licence register does not cover every visual-review shot.")
    blocked = [row.get("shot_id", "unknown") for row in rows if row.get("use_status") == "blocked"]
    if blocked:
        raise ValueError("Asset licence register contains blocked assets: " + ", ".join(blocked[:12]))
    return {"shot_count": len(visual["shots"]), "licence_rows": len(rows),
            "research_claim_count": research.get("claim_count"),
            "research_source_count": research.get("source_count")}


def upload_reviewed_artifact(artifact_dir: Path, source_run_id: str, human_reviewed: bool) -> dict:
    if not human_reviewed:
        raise ValueError("Human review confirmation is required before upload.")
    if not source_run_id.isdigit():
        raise ValueError("Source workflow run ID must be numeric.")
    root = artifact_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Artifact directory does not exist: {root}")

    metadata_path, metadata = _find_release_metadata(root)
    video_path, probe = _find_video(root)
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
    parser.add_argument("--human-reviewed", action="store_true")
    args = parser.parse_args()
    try:
        upload_reviewed_artifact(args.artifact_dir, args.source_run_id, args.human_reviewed)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
