"""Upload orchestration shared by the CLI, the daily runner and the dashboard.

One code path now performs, in order: release gate → duplicate check (ledger,
database, channel marker) → PRIVATE upload to the verified channel → read-back
of the stored privacy → thumbnail/playlist/comment (best effort) → ledger and
database bookkeeping → notification. ``run.py upload`` and
``run_daily.py --upload-latest`` previously carried two diverging copies of this
logic, neither of which forced private uploads or prevented duplicates.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core import config as cfg
from core import dedupe


class DuplicateUpload(RuntimeError):
    """The same subject or job has already been uploaded."""


FINANCE_DISCLAIMERS = {
    "english": "This video is for educational purposes only. It is not investment advice.",
    "hinglish": "Yeh video sirf educational jaankari ke liye hai. Yeh investment advice nahi hai.",
    "hindi": "यह वीडियो सिर्फ़ शैक्षणिक जानकारी के लिए है। यह निवेश सलाह नहीं है।",
}

PLAYLISTS = {
    "finance": "Finance Explained | {channel}",
    "markets": "Share Market | {channel}",
    "money": "Paise ki Samajh | {channel}",
    "power": "पावर के नियम | {channel}",
    "human": "इंसानी व्यवहार | {channel}",
    "physics": "Physics Explained | {channel}",
    "engineering": "Engineering Explained | {channel}",
    "technology": "Technology Explained | {channel}",
    "science": "Science Explained | {channel}",
    "news": "News Explained | {channel}",
}


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upload_marker(row: dict) -> str:
    """Stable idempotency marker for one subject in one format."""
    from core.db import normalize_format
    fmt = normalize_format(row.get("format"))
    key = row.get("topic_key") or dedupe.topic_key(row.get("title") or f"job-{row.get('id')}")
    return dedupe.upload_marker(fmt, key)


def build_metadata(row: dict, script: dict, build_dir: Path) -> dict:
    """Title, description and tags for the upload (deterministic; no model call)."""
    from core.db import normalize_format
    from core.script_engine import generate_seo
    fmt = normalize_format(row.get("format"))
    language = str(row.get("language") or "").strip().lower() or None
    chapter_starts = None
    timing = build_dir / "chapter_timing.json"
    if timing.is_file():
        try:
            chapter_starts = [float(item["start"]) for item in json.loads(timing.read_text(encoding="utf-8"))]
        except (OSError, ValueError, KeyError, TypeError):
            chapter_starts = None
    seo = generate_seo(script, language=language, chapter_starts=chapter_starts)
    title = str(row.get("title") or script.get("title") or "Untitled").strip()
    if fmt in {"shorts", "news_short"} and "#shorts" not in title.lower():
        title = title[:91].rstrip() + " #Shorts"
    title = title[:100]

    release = {}
    release_path = build_dir / "release_metadata.json"
    if release_path.is_file():
        try:
            release = json.loads(release_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            release = {}
    parts = [str(release.get("description") or script.get("description") or "").strip()
             or f"{cfg.CHANNEL_NAME} - clear explanations of science, engineering and how things work."]
    if seo.get("chapters"):
        parts.append("Chapters:\n" + "\n".join(seo["chapters"]))
    category = str(row.get("category") or script.get("category") or "").lower()
    if category in {"finance", "markets", "money"}:
        parts.append(FINANCE_DISCLAIMERS.get(seo.get("language"), FINANCE_DISCLAIMERS["english"]))
    sources = [s for s in (script.get("sources") or []) if isinstance(s, dict) and s.get("url")]
    if sources and "http" not in parts[0]:
        parts.append("Sources:\n" + "\n".join(
            f"- {str(s.get('title') or s.get('publisher') or 'Source')[:90]}: {s['url']}" for s in sources[:12]))
    credits = build_dir / "credits.txt"
    if fmt != "longform" and credits.is_file():  # documentaries embed credits in release metadata
        try:
            text = credits.read_text(encoding="utf-8").strip()
            lines = [line for line in text.splitlines()
                     if line.strip() and "DOCUMENTARY SOURCE CREDITS" not in line
                     and not line.startswith("No externally sourced")
                     and "Human rights and context review" not in line]
            if lines:
                parts.append("Visual credits:\n" + "\n".join(lines[:15]))
        except OSError:
            pass
    manifest_path = build_dir / "asset_manifest.json"
    synthetic = True
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("ai_generated_count"):
                parts.append("Some visuals in this video are AI-generated illustrations.")
        except (OSError, ValueError):
            pass
    hashtags = " ".join(seo.get("hashtags", [])[:3])
    if fmt in {"shorts", "news_short"}:
        hashtags = ("#Shorts " + hashtags).strip()
    if hashtags:
        parts.append(hashtags)
    return {"title": title, "description": "\n\n".join(p for p in parts if p)[:4800],
            "tags": seo["tags"], "pinned_comment": seo["pinned_comment"],
            "category_id": "25" if fmt == "news_short" else "27",
            "contains_synthetic": synthetic, "language": seo.get("language")}


def _existing_upload(row: dict, ledger: dedupe.Ledger, kind: str) -> dict | None:
    hit = ledger.find(kind, str(row.get("title") or ""), key=str(row.get("topic_key") or ""))
    if hit and hit.get("youtube_id") and hit.get("job_id") != row.get("id"):
        return hit
    # The job database also knows about uploads made before the ledger existed.
    from core.db import normalize_format
    for known in dedupe.known_titles_from_db():
        if (known.get("youtube_id") and known.get("job_id") != row.get("id")
                and normalize_format(known.get("format")) == kind
                and dedupe.is_same_subject(str(row.get("title") or ""), known["title"])):
            return known
    return None


def publish_job(job_id: int, *, wait: bool = False, notify: bool = True, dry_run: bool = False) -> dict:
    """Upload one finished job privately. Returns a result dict; raises on a block."""
    from core.db import get_video, update_video, normalize_format, record_stage
    from core.notify import send
    from core.release_gate import assert_uploadable

    row = get_video(job_id)
    if not row:
        raise LookupError(f"Job #{job_id} was not found.")
    fmt = normalize_format(row.get("format"))
    build_dir = cfg.BUILD_DIR / f"job_{job_id}"
    if row.get("youtube_id"):
        return {"job_id": job_id, "youtube_id": row["youtube_id"], "status": "already_uploaded",
                "url": _url(fmt, row["youtube_id"])}

    gate = assert_uploadable(row, build_dir)
    ledger = dedupe.Ledger()
    duplicate = _existing_upload(row, ledger, fmt)
    if duplicate and not _flag("ALLOW_DUPLICATE_UPLOAD"):
        raise DuplicateUpload(
            f"'{row.get('title')}' duplicates '{duplicate.get('title')}' already uploaded as "
            f"{duplicate.get('youtube_id')}; refusing a second upload. Set ALLOW_DUPLICATE_UPLOAD=1 to override.")

    script = json.loads(row.get("script_json") or "{}")
    metadata = build_metadata(row, script, build_dir)
    marker = upload_marker(row)
    (build_dir / "upload_metadata.json").write_text(json.dumps(
        {**metadata, "marker": marker, "privacy": "private", "prepared_at": _now()},
        ensure_ascii=False, indent=2), encoding="utf-8")
    if dry_run:
        return {"job_id": job_id, "status": "dry_run", "gate": gate, "metadata": metadata, "marker": marker}

    from core import uploader
    video_path = Path(row["video_path"])
    print(f"Uploading (private): {metadata['title']}")
    try:
        # ``marker`` makes the upload idempotent: an earlier attempt of this job
        # that reached YouTube but crashed before bookkeeping is found, not repeated.
        video_id = uploader.upload(
            video_path, metadata["title"], metadata["description"], metadata["tags"],
            category_id=metadata["category_id"], contains_synthetic=metadata["contains_synthetic"],
            privacy_status="private", disable_scheduling=not uploader.public_publish_allowed(),
            marker=marker)
    except Exception as exc:
        record_stage(job_id, "upload", "failed", error=f"{type(exc).__name__}: {exc}"[:800])
        if notify:
            send(f"❌ Upload failed for '{metadata['title']}': {str(exc)[:300]}")
        raise

    # Read back what YouTube stored; never trust the request alone.
    privacy, publish_at = "private", None
    # True only if YouTube answered with a stored privacy value.  Reports use this
    # to say "verified" instead of assuming the requested privacy was applied.
    privacy_verified = False
    try:
        stored = uploader.get_video_status(video_id)
        privacy_verified = bool(stored.get("privacy_status"))
        privacy = stored.get("privacy_status") or privacy
        publish_at = stored.get("publish_at")
        if privacy != "private" and not uploader.public_publish_allowed():
            uploader.set_video_privacy(video_id, "private")
            privacy = "private"
            if notify:
                send(f"⚠️ {video_id} was not private after upload; it has been set back to private.")
    except Exception as exc:
        print(f"WARNING: could not read back the stored privacy for {video_id}: {exc}")

    update_video(job_id, youtube_id=video_id, status="uploaded", privacy_status=privacy,
                 uploaded_at=_now(), error=None)
    ledger.record(fmt, str(row.get("title") or metadata["title"]), key=str(row.get("topic_key") or ""),
                  job_id=job_id, youtube_id=video_id, privacy_status=privacy, status="uploaded",
                  source_refs=[r for r in str(row.get("source_ref") or "").split(",") if r])
    record_stage(job_id, "upload", "done", detail={"youtube_id": video_id, "privacy": privacy})

    warnings = []
    if not privacy_verified:
        warnings.append("privacy: the stored privacy status could not be read back from YouTube; "
                        "confirm in YouTube Studio that the video is private")
    thumbnail = row.get("thumb_path")
    if thumbnail and Path(thumbnail).is_file():
        try:
            uploader.set_video_thumbnail(video_id, Path(thumbnail))
        except Exception as exc:
            warnings.append(f"thumbnail: {str(exc)[:160]}")
    category = str(row.get("category") or "").lower()
    playlist = PLAYLISTS.get(category, "{channel}").format(channel=cfg.CHANNEL_NAME)
    if not _flag("SKIP_PLAYLIST"):
        try:
            uploader.add_to_playlist(video_id, playlist)
        except Exception as exc:
            warnings.append(f"playlist: {str(exc)[:160]}")
    if _flag("POST_PINNED_COMMENT"):
        try:
            uploader.post_comment(video_id, metadata["pinned_comment"])
        except Exception as exc:
            warnings.append(f"comment: {str(exc)[:160]}")
    processing = None
    if wait:
        processing = uploader.wait_processed(video_id)
        if processing != "ok":
            update_video(job_id, status=f"youtube_{processing}")
            if notify:
                send(f"❌ YouTube processing did not complete ({processing}) for: {metadata['title']}")
            raise RuntimeError(f"YouTube processing did not complete: {processing}")
    url = _url(fmt, video_id)
    result = {"job_id": job_id, "youtube_id": video_id, "url": url, "privacy": privacy,
              "privacy_verified": privacy_verified,
              "publish_at": publish_at, "status": "uploaded", "warnings": warnings,
              "processing": processing, "title": metadata["title"]}
    (build_dir / "upload_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for warning in warnings:
        print(f"WARNING: uploaded, but {warning}")
    if notify:
        send(f"✅ Uploaded ({privacy}): {metadata['title']}\n{url}")
    print(f"Uploaded ({privacy}): {url}")
    return result


def _url(fmt: str, video_id: str) -> str:
    return (f"https://youtube.com/shorts/{video_id}" if fmt in {"shorts", "news_short"}
            else f"https://youtube.com/watch?v={video_id}")


def latest_unuploaded(video_format: str | None = None) -> dict | None:
    from core.db import list_videos, normalize_format
    wanted = normalize_format(video_format) if video_format else None
    for row in list_videos(limit=100):
        if row.get("youtube_id") or row.get("status") != "done" or not row.get("video_path"):
            continue
        if wanted and normalize_format(row.get("format")) != wanted:
            continue
        if Path(row["video_path"]).exists():
            return row
    return None


def publish_latest(video_format: str | None = None, **kwargs) -> dict:
    row = latest_unuploaded(video_format)
    if not row:
        raise RuntimeError(f"No completed, unuploaded {video_format or ''} video was found in the database.")
    return publish_job(row["id"], **kwargs)
