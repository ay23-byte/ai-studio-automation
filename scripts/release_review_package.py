"""Release an approved shared Shorts package as PRIVATE only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.apply_review_decisions import apply_decisions
from scripts.validate_review_package import validate_package


def release_package(package_dir, decisions_path, expected_sha256, confirm_private_upload=False):
    if confirm_private_upload is not True:
        raise ValueError("explicit confirm_private_upload=true required")

    root = Path(package_dir).resolve()
    manifest = json.loads((root / "production_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("mode") not in {"shortform", "daily_news"}:
        raise ValueError("shared Short release only accepts shortform or daily_news packages")

    video = (root / str(manifest.get("video_file") or "video.mp4")).resolve()
    if root not in video.parents or not video.is_file():
        raise ValueError("manifest video missing or escapes package")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    if digest.lower() != str(expected_sha256).lower():
        raise ValueError("MP4 SHA-256 mismatch")

    apply_decisions(root, decisions_path)
    summary = validate_package(root)
    (root / "package_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"
    )
    if summary.get("status") != "APPROVED_FOR_UPLOAD" or summary.get("publication_eligible") is not True:
        raise ValueError("review package not approved: " + json.dumps(summary.get("required_human_reviews", [])))

    metadata_path = root / "youtube_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    title = str(metadata.get("title") or manifest.get("topic") or "Engineering Physics Short")
    description = str(metadata.get("description") or manifest.get("topic") or "")
    tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else ["Engineering Physics", "Shorts"]
    marker = "AI-STUDIO-REVIEWED-SHA256:" + digest

    from core.uploader import upload
    video_id = upload(
        video, title, description, tags,
        category_id=str(metadata.get("category_id") or "27"),
        contains_synthetic=bool(metadata.get("synthetic_media", True)),
        privacy_status="private", disable_scheduling=True,
        marker=marker, authorized=False,
    )
    result = {
        "video_id": video_id,
        "url": "https://www.youtube.com/watch?v=" + video_id,
        "privacy": "private",
        "scheduled": False,
        "public_publish_performed": False,
        "mode": manifest.get("mode"),
        "topic": manifest.get("topic"),
        "video_sha256": digest,
    }
    (root / "youtube-upload-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--confirm-private-upload", action="store_true")
    args = parser.parse_args()
    result = release_package(
        args.package_dir, args.decisions, args.video_sha256,
        confirm_private_upload=args.confirm_private_upload,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
