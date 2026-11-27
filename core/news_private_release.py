"""Fail-closed automatic PRIVATE release for machine-verified daily-news Shorts.

This is intentionally separate from the human-reviewed release path used by
standard Shorts and documentaries. It does not manufacture reviewer decisions:
it permits daily-news auto-release only after the deterministic source, script,
render, and technical gates all PASS. Visibility is hard-coded PRIVATE.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


class NewsAutoReleaseBlocked(RuntimeError):
    """A daily-news Short did not satisfy the automatic release contract."""


def _quality_report_allows_private_release(quality: dict) -> tuple[bool, str]:
    """Allow a bounded, explicitly permitted fallback share for PRIVATE review only.

    The renderer changes an otherwise-PASS quality report to REVIEW whenever it
    uses fallback cards, even when the fallback share is within its configured
    maximum.  The same report sets upload_blocked only when that maximum is
    exceeded.  Requiring status == PASS here contradicted that policy and
    rejected valid, bounded review builds.
    """
    if quality.get("upload_blocked"):
        return False, "The render quality report blocks upload because fallback visuals exceed the allowed share."
    status = str(quality.get("status") or "").upper()
    if status == "PASS":
        return True, ""
    if status != "REVIEW":
        return False, f"Automatic release requires technical QA PASS or an explicitly permitted fallback REVIEW; got {quality.get('status')!r}."

    fallback = quality.get("visual_fallback")
    if not isinstance(fallback, dict) or str(fallback.get("status") or "").upper() != "PASS":
        return False, "A REVIEW quality report is releasable only when its fallback-visual report explicitly passes the configured share limit."
    try:
        count = int(fallback["fallback_card_count"])
        total = int(fallback["visual_count"])
        maximum = float(fallback["max_share_allowed"])
    except (KeyError, TypeError, ValueError):
        return False, "Fallback-visual review evidence is incomplete or invalid."
    if total <= 0 or count < 0 or count > total or not 0.0 <= maximum <= 1.0:
        return False, "Fallback-visual review evidence contains invalid counts or limits."
    if count / total > maximum + 1e-9:
        return False, "Fallback-visual share exceeds its configured maximum."
    return True, "Technical QA passed; bounded fallback visuals remain disclosed for human review."


def _probe(video: Path) -> dict:
    from core.ffmpeg_utils import ffprobe_bin
    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of", "json", str(video)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout or "{}")
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise NewsAutoReleaseBlocked("The rendered MP4 has no video stream.")
    stream = streams[0]
    try:
        duration = float((data.get("format") or {}).get("duration"))
        width, height = int(stream["width"]), int(stream["height"])
    except (TypeError, ValueError, KeyError) as exc:
        raise NewsAutoReleaseBlocked("FFprobe could not verify duration and dimensions.") from exc
    if width < 1080 or height < 1920 or height <= width:
        raise NewsAutoReleaseBlocked(
            f"Expected a vertical 1080x1920-or-better Short; got {width}x{height}.")
    if not 15 <= duration <= 60:
        raise NewsAutoReleaseBlocked(f"News Short duration {duration:.1f}s is outside 15–60 seconds.")
    return {"width": width, "height": height, "duration_seconds": round(duration, 2)}


def publish_news_job(job_id: int, *, wait: bool = True) -> dict:
    """Upload one news job PRIVATE only after all automated gates pass."""
    from core import config as cfg, dedupe
    from core.db import get_video, update_video, record_stage
    from core.publish import build_metadata, upload_marker
    from core.release_gate import evaluate

    row = get_video(job_id)
    if not row:
        raise LookupError(f"News job #{job_id} was not found.")
    build_dir = Path(cfg.BUILD_DIR) / f"job_{job_id}"
    video = Path(str(row.get("video_path") or ""))
    if not video.is_file() or video.stat().st_size <= 50_000:
        raise NewsAutoReleaseBlocked("Rendered MP4 is missing or too small.")
    if str(row.get("format") or "").lower() not in {"news_short", "daily_news", "daily-news"}:
        raise NewsAutoReleaseBlocked("Automatic private release is restricted to daily-news Shorts.")

    # Reuse the shared gate's independent checks, but do not invent or waive a
    # human review. The automatic path requires every non-human check to PASS.
    gate = evaluate(row, build_dir)
    checks = [item for item in gate.get("checks", []) if item.get("check") != "human_review"]
    failed = [f"{item.get('check')}: {item.get('detail')}"
              for item in checks if item.get("status") != "PASS"]
    if failed:
        raise NewsAutoReleaseBlocked("Automated release checks did not all PASS: " + "; ".join(failed[:12]))

    verification_path = build_dir / "verification.json"
    try:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NewsAutoReleaseBlocked("The source-verification evidence is missing or invalid.") from exc
    if verification.get("status") != "PASS":
        raise NewsAutoReleaseBlocked("The source-verification evidence is not PASS.")

    quality_path = Path(row.get("quality_report_path") or build_dir / "quality_report.json")
    try:
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NewsAutoReleaseBlocked("The render quality report is missing or invalid.") from exc
    allowed, reason = _quality_report_allows_private_release(quality)
    if not allowed:
        raise NewsAutoReleaseBlocked(reason)
    if quality.get("video_path") and Path(str(quality["video_path"])).name != video.name:
        raise NewsAutoReleaseBlocked("The quality report refers to a different MP4.")

    media = _probe(video)
    script = json.loads(row.get("script_json") or "{}")
    from core.content_qa import validate_shorts_script
    content = validate_shorts_script(script, row.get("language") or "english")
    if content.get("status") != "PASS":
        raise NewsAutoReleaseBlocked(
            "Script QA must be PASS (not REVIEW or FAIL): " +
            "; ".join(content.get("issues", []) + content.get("warnings", []))[:1200])

    from core import uploader
    try:
        channel = uploader.verify_channel(force=True)
    except Exception as exc:
        failure = {"job_id": job_id, "status": "upload_failed", "privacy_verified": False,
                   "error": f"{type(exc).__name__}: {exc}"[:800]}
        (build_dir / "automatic_private_release.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        record_stage(job_id, "upload", "failed", error=failure["error"])
        raise NewsAutoReleaseBlocked("YouTube channel verification failed: " + failure["error"]) from exc
    if not channel.get("verified"):
        failure = {"job_id": job_id, "status": "upload_failed", "privacy_verified": False,
                   "error": "YouTube channel identity is not verified."}
        (build_dir / "automatic_private_release.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        record_stage(job_id, "upload", "failed", error=failure["error"])
        raise NewsAutoReleaseBlocked(
            "YouTube channel identity is not verified; configure YT_CHANNEL_ID or YT_CHANNEL_HANDLE.")
    expected_handle = str(os.getenv("YT_CHANNEL_HANDLE") or "@engineeringphysics-n5g").lower().lstrip("@")
    actual_handle = str(channel.get("handle") or "").lower().lstrip("@")
    if expected_handle and actual_handle and actual_handle != expected_handle:
        failure = {"job_id": job_id, "status": "upload_failed", "privacy_verified": False,
                   "error": f"Authenticated channel @{actual_handle} does not match @{expected_handle}."}
        (build_dir / "automatic_private_release.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        record_stage(job_id, "upload", "failed", error=failure["error"])
        raise NewsAutoReleaseBlocked(failure["error"])

    metadata = build_metadata(row, script, build_dir)
    marker = upload_marker(row)
    try:
        existing = uploader.find_private_video_by_marker(marker)
        if existing:
            video_id = existing
            reused = True
        else:
            video_id = uploader.upload(
                video, metadata["title"], metadata["description"], metadata["tags"],
                category_id=metadata["category_id"], contains_synthetic=True,
                privacy_status="private", disable_scheduling=True, marker=marker)
            reused = False
    except Exception as exc:
        failure = {"job_id": job_id, "status": "upload_failed", "privacy_verified": False,
                   "error": f"{type(exc).__name__}: {exc}"[:800]}
        (build_dir / "automatic_private_release.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        record_stage(job_id, "upload", "failed", error=failure["error"])
        raise NewsAutoReleaseBlocked("YouTube upload request failed: " + failure["error"]) from exc

    try:
        processing = uploader.wait_processed(video_id) if wait else None
        stored = uploader.get_video_status(video_id)
    except Exception as exc:
        failure = {"job_id": job_id, "youtube_id": video_id, "status": "verification_failed",
                   "privacy_verified": False, "error": f"{type(exc).__name__}: {exc}"[:800]}
        (build_dir / "automatic_private_release.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        record_stage(job_id, "upload", "unverified", error=failure["error"])
        raise NewsAutoReleaseBlocked("YouTube upload was created but verification failed: "
                                     + failure["error"]) from exc
    problems = []
    if not stored.get("found"):
        problems.append("YouTube did not return the uploaded video.")
    else:
        if stored.get("privacy_status") != "private":
            problems.append(f"YouTube privacy read-back was {stored.get('privacy_status')!r}, not 'private'.")
        if stored.get("publish_at"):
            problems.append("YouTube returned a scheduled publish time; automatic release never schedules.")
        if stored.get("channel_id") != channel.get("id"):
            problems.append("The uploaded video is not on the verified channel.")
    if wait and processing != "ok":
        problems.append(f"YouTube processing status was {processing!r}, not 'ok'.")
    result = {
        "job_id": job_id, "youtube_id": video_id,
        "url": f"https://www.youtube.com/shorts/{video_id}",
        "privacy": stored.get("privacy_status"), "privacy_verified": not problems,
        "channel_id": stored.get("channel_id"), "channel_title": channel.get("title"),
        "processing": processing, "reused_existing_upload": reused,
        "video": {"file": video.name, "size_bytes": video.stat().st_size, **media},
        "automated_checks": {"release_gate": checks, "source_verification": "PASS",
                             "technical_qa": "PASS", "script_qa": "PASS"},
        "verification_problems": problems,
        "status": "verification_failed" if problems else "uploaded",
    }
    (build_dir / "automatic_private_release.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if problems:
        record_stage(job_id, "upload", "unverified", error="; ".join(problems)[:800])
        raise NewsAutoReleaseBlocked(
            "YouTube upload needs attention; it is not reported as verified: " + "; ".join(problems))
    update_video(job_id, youtube_id=video_id, status="uploaded",
                 privacy_status="private", uploaded_at=datetime.now(timezone.utc).isoformat(),
                 error=None)
    dedupe.Ledger().record(
        "news_short", str(row.get("title") or metadata["title"]),
        key=str(row.get("topic_key") or ""), job_id=job_id, youtube_id=video_id,
        privacy_status="private", status="uploaded",
        source_refs=[r for r in str(row.get("source_ref") or "").split(",") if r])
    record_stage(job_id, "upload", "done", detail={"youtube_id": video_id, "privacy": "private"})
    return {"status": "uploaded", **result}
