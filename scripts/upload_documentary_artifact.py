"""Upload one human-reviewed documentary artifact to YouTube as Private only."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from core.uploader import upload, wait_processed


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
    reports = sorted(root.glob("build/live_documentary_validation/mp4-probe-report.json"))
    if len(reports) != 1:
        raise ValueError("The render artifact must contain exactly one MP4 probe report.")
    report = _load_json(reports[0])
    candidates = []
    for item in report.get("videos") or []:
        if not (
            item.get("bytes", 0) > 100_000
            and item.get("has_video") is True
            and item.get("has_audio") is True
            and item.get("duration_seconds", 0) > 0
        ):
            continue
        rel = Path(str(item.get("path") or ""))
        if rel.is_absolute() or ".." in rel.parts:
            continue
        path = root / rel
        if path.is_file() and path.stat().st_size > 100_000:
            candidates.append((path, item))
    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one MP4 that passed the render's recorded stream/size checks; "
            f"found {len(candidates)}. Refusing to select a clip or arbitrary export."
        )
    return candidates[0]


def upload_reviewed_artifact(artifact_dir: Path, source_run_id: str, human_reviewed: bool) -> dict:
    if not human_reviewed:
        raise ValueError("Human review confirmation is required before upload.")
    if not source_run_id.isdigit():
        raise ValueError("Source workflow run ID must be numeric.")
    root = artifact_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Artifact directory does not exist: {root}")

    _, metadata = _find_release_metadata(root)
    video_path, probe = _find_video(root)
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()

    # Hard-code privacy and disable scheduling: this workflow must never publish.
    video_id = upload(
        video_path,
        metadata["title"],
        metadata["description"],
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
    }
    output = root / "youtube-upload-result.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if processing not in {"ok", "timeout"}:
        raise RuntimeError(f"YouTube processing ended with status: {processing}")
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
