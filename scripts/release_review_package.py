"""Release one human-approved review package to YouTube as PRIVATE.

The single release path for documentaries, standard Shorts and daily-news
Shorts.  It uploads only when every independent check passes for the exact
MP4 that was reviewed, then reads the stored privacy back from YouTube.

    python scripts/release_review_package.py \
        --package-dir <dir> --decisions <review_decisions.json> \
        --video-sha256 <sha256> --mode shortform --source-run-id <run id> \
        --confirm-private-upload

``release_report.json`` is written for every outcome, including a refusal.
Exit codes: 0 uploaded and verified private; 2 blocked before any upload;
3 uploaded but verification failed (inspect the video manually); 1 error.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import review_contract as contract

MARKER_PREFIX = "AI-STUDIO-REVIEWED-SHA256:"
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ReleaseBlocked(ValueError):
    """Refused before any upload was attempted."""


class ReleaseUnverified(RuntimeError):
    """Something was uploaded but could not be verified as private on the right channel."""


def _load_decisions(decisions) -> dict:
    if isinstance(decisions, dict):
        if not decisions:
            raise ReleaseBlocked("No review decisions were supplied.")
        return decisions
    try:
        text = Path(decisions).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseBlocked(f"Cannot read the review decisions: {exc}") from exc
    try:
        return contract.load_decisions_text(text)
    except contract.ContractError as exc:
        raise ReleaseBlocked(str(exc)) from exc


def _write_report(report_dir: Path, report: dict) -> None:
    contract.write_json(report_dir / contract.RELEASE_REPORT_NAME, report)
    (report_dir / "release_report.md").write_text(_markdown(report), encoding="utf-8")


def _markdown(report: dict) -> str:
    text = contract.summarize(report)
    upload = report.get("upload") or {}
    text += f"\n**Outcome: {report.get('outcome')}**\n\n"
    if report.get("refusal"):
        text += "Refused: " + str(report["refusal"]).replace("\n", " ")[:1500] + "\n\n"
    if upload.get("video_id"):
        text += (f"- Video: {upload.get('url')}\n- Privacy read back from YouTube: "
                 f"`{upload.get('privacy_status_read_back')}`\n- Channel: {upload.get('channel_title')} "
                 f"(`{upload.get('channel_id')}`)\n- Duplicate upload avoided: {upload.get('reused_existing_upload')}\n")
    return text


def release_package(package_dir, decisions, expected_sha256, confirm_private_upload: bool = False, *,
                    expected_mode: str | None = None, source_run_id: str | None = None,
                    expected_job_id: str | None = None, requested_privacy: str | None = "private",
                    publish_at: str | None = "", report_dir=None, uploader=None,
                    processing_wait_seconds: int = 600) -> dict:
    """Validate everything, upload PRIVATE, read the result back.  Returns the release report."""
    root = Path(package_dir).resolve()
    report_dir = Path(report_dir).resolve() if report_dir else root
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": contract.RELEASE_REPORT_SCHEMA_VERSION, "evaluated_at": contract.now_iso(),
              "allowed": False, "checks": [], "blocking": [], "outcome": "blocked", "upload": None,
              "request": {"mode": expected_mode, "source_run_id": source_run_id, "job_id": expected_job_id,
                          "video_sha256": str(expected_sha256 or "").lower(),
                          "privacy": requested_privacy, "publish_at": publish_at or None,
                          "release_run_id": os.environ.get("GITHUB_RUN_ID"),
                          "requested_by": os.environ.get("GITHUB_ACTOR")}}

    def refuse(message: str):
        report["refusal"] = message
        _write_report(report_dir, report)
        raise ReleaseBlocked(message)

    # --- the request itself ------------------------------------------------------
    if confirm_private_upload is not True:
        refuse("confirm_private_upload must be explicitly true; nothing was uploaded.")
    if str(requested_privacy or "private").strip().lower() != "private":
        refuse(f"This workflow uploads PRIVATE only; a {requested_privacy!r} release was requested. "
               "Publishing is a separate, deliberate action in YouTube Studio.")
    if str(publish_at or "").strip():
        refuse("A scheduled release was requested (publish time set). This workflow never schedules.")
    if not SHA_RE.match(str(expected_sha256 or "")):
        refuse("video_sha256 must be the 64-character SHA-256 of the reviewed MP4.")
    if source_run_id is not None and not str(source_run_id).isdigit():
        refuse("source_run_id must be the numeric id of the build run.")
    if not root.is_dir():
        refuse(f"The review package directory does not exist: {root.name}")

    # --- every independent check --------------------------------------------------
    try:
        loaded = _load_decisions(decisions)
    except ReleaseBlocked as exc:
        refuse(str(exc))
    evaluation = contract.evaluate_release(
        root, loaded, expected_sha256=str(expected_sha256).lower(), expected_mode=expected_mode,
        expected_job_id=expected_job_id, expected_run_id=str(source_run_id) if source_run_id else None)
    report.update({key: evaluation[key] for key in evaluation if key not in {"schema_version"}})
    report["upload"] = None
    if not evaluation["allowed"]:
        report["outcome"] = "blocked"
        refuse("Release blocked; nothing was uploaded:\n- " + "\n- ".join(evaluation["blocking"][:60]))

    manifest = contract.read_manifest(root)
    publish = manifest.get("publish") if isinstance(manifest.get("publish"), dict) else {}
    title = str(publish.get("title") or manifest.get("title") or manifest.get("topic") or "").strip()
    if not title:
        refuse("The package has no title to publish under.")
    video = contract.inside(root, manifest["video"]["file"])
    digest = evaluation["video_sha256"]
    marker = MARKER_PREFIX + digest

    # --- upload (private, never scheduled) and read back ---------------------------
    if uploader is None:
        from core import uploader as uploader  # imported late: needs the Google client libraries
    upload = {"marker": marker, "video_id": None, "reused_existing_upload": None}
    report["upload"] = upload
    try:
        channel = uploader.verify_channel(force=True)
    except Exception as exc:
        report["outcome"] = "blocked"
        refuse(f"YouTube channel identity could not be verified; nothing was uploaded: {exc}")
    if not channel.get("verified"):
        refuse("YouTube channel verification is switched off or unconfigured (YT_CHANNEL_ID / "
               "YT_CHANNEL_HANDLE / YT_CHANNEL_VERIFY). A release needs a verified channel; nothing was uploaded.")
    upload.update({"channel_id": channel.get("id"), "channel_title": channel.get("title"),
                   "channel_handle": channel.get("handle")})
    try:
        existing = uploader.find_private_video_by_marker(marker)
    except Exception as exc:
        refuse(f"Could not check YouTube for an earlier upload of this video; refusing to risk a duplicate: {exc}")
    description = str(publish.get("description") or "").rstrip()
    try:
        if existing:
            video_id = existing
            upload["reused_existing_upload"] = True
            print(f"This exact video was already uploaded ({video_id}); not uploading it again.")
        else:
            upload["reused_existing_upload"] = False
            video_id = uploader.upload(
                video, title, (description + "\n\n" + marker).strip(), publish.get("tags") or [],
                category_id=str(publish.get("category_id") or "27"),
                privacy_status="private", disable_scheduling=True)
    except Exception as exc:
        report["outcome"] = "upload_failed"
        report["refusal"] = f"The upload failed: {type(exc).__name__}: {exc}"
        _write_report(report_dir, report)
        raise
    upload.update({"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}",
                   "title": title[:100], "file": manifest["video"]["file"], "sha256": digest,
                   "size_bytes": video.stat().st_size})
    report["outcome"] = "uploaded_unverified"
    _write_report(report_dir, report)  # the id survives even if verification crashes

    problems = []
    try:
        upload["processing_status"] = uploader.wait_processed(video_id, max_wait=processing_wait_seconds)
        stored = uploader.get_video_status(video_id)
    except Exception as exc:
        stored = {"found": False}
        problems.append(f"Could not read the video back from YouTube: {type(exc).__name__}: {exc}")
    upload.update({"privacy_status_read_back": stored.get("privacy_status"),
                   "publish_at_read_back": stored.get("publish_at"),
                   "upload_status_read_back": stored.get("upload_status"),
                   "channel_id_read_back": stored.get("channel_id")})
    if not stored.get("found"):
        problems.append("YouTube did not return the uploaded video.")
    else:
        if stored.get("privacy_status") != "private":
            problems.append(f"YouTube stored privacy {stored.get('privacy_status')!r}, not 'private'.")
        if stored.get("publish_at"):
            problems.append(f"YouTube shows a scheduled publish time ({stored.get('publish_at')}).")
        if channel.get("id") and stored.get("channel_id") != channel.get("id"):
            problems.append(f"The video is on channel {stored.get('channel_id')}, not the verified "
                            f"channel {channel.get('id')}.")
    if upload.get("processing_status") not in (None, "ok"):
        problems.append(f"YouTube processing status: {upload.get('processing_status')}.")
    upload["verification_problems"] = problems
    report["outcome"] = "uploaded_private_verified" if not problems else "uploaded_unverified"
    report["completed_at"] = contract.now_iso()
    _write_report(report_dir, report)
    # Kept for tools that read the earlier result file name.
    contract.write_json(report_dir / "youtube-upload-result.json", {
        "video_id": video_id, "url": upload["url"], "privacy_status": stored.get("privacy_status"),
        "privacy_verified": not problems, "channel_id": stored.get("channel_id"),
        "job_id": report.get("job_id"), "mode": report.get("mode"), "sha256": digest,
        "source_run_id": source_run_id, "reused_existing_upload": upload["reused_existing_upload"]})
    if problems:
        raise ReleaseUnverified("The video was uploaded but verification failed; check it in YouTube Studio now: "
                                + " ".join(problems))
    try:
        manifest["upload"] = {"status": "uploaded_private", "video_id": video_id, "privacy_status": "private",
                              "channel_id": stored.get("channel_id"), "uploaded_at": report["completed_at"]}
        contract.write_json(root / contract.MANIFEST_NAME, manifest)
    except OSError:
        pass
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--package-dir")
    where.add_argument("--artifact-root",
                       help="Downloaded run artifacts; must contain exactly one review package")
    parser.add_argument("--decisions", required=True, help="review_decisions.json exported from the dashboard")
    parser.add_argument("--video-sha256", required=True)
    parser.add_argument("--mode", choices=sorted(contract.MODES), default=None)
    parser.add_argument("--source-run-id", default=None)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--report-dir", default=None)
    parser.add_argument("--confirm-private-upload", action="store_true")
    args = parser.parse_args(argv)
    package_dir = args.package_dir
    if args.artifact_root:
        packages = contract.find_packages(args.artifact_root)
        if len(packages) != 1:
            print(f"::error::Release blocked. Nothing was uploaded. Expected exactly one review package in the "
                  f"artifacts of the source run, found {len(packages)}. A build made before the shared review "
                  "package existed cannot be released; rebuild it.")
            return 2
        package_dir = packages[0]
        print(f"Review package: {package_dir}")
    try:
        report = release_package(
            package_dir, args.decisions, args.video_sha256, args.confirm_private_upload,
            expected_mode=args.mode, source_run_id=args.source_run_id, expected_job_id=args.job_id,
            requested_privacy=os.environ.get("PRIVACY") or "private",
            publish_at=os.environ.get("PUBLISH_AT_IST") or "", report_dir=args.report_dir)
    except ReleaseBlocked as exc:
        print(f"::error::Release blocked. Nothing was uploaded. {str(exc).splitlines()[0]}")
        print(str(exc))
        return 2
    except ReleaseUnverified as exc:
        print(f"::error::{exc}")
        return 3
    except Exception as exc:  # upload or API failure: report it, do not hide it
        print(f"::error::Release failed: {type(exc).__name__}: {exc}")
        return 1
    upload = report["upload"]
    print(f"Uploaded PRIVATE and verified: {upload['url']} (privacy read back: "
          f"{upload['privacy_status_read_back']}, channel {upload['channel_title']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
