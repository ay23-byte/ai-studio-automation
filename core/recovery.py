"""Bounded recovery for a production build that failed part-way.

A build that dies in the render or a provider call has usually already paid for
its expensive stages (script, narration, visuals); the job checkpoint records
them.  On a GitHub runner the checkout disappears when the job ends, so the only
chance to reuse that work is inside the same job.  This module decides whether a
failed job is worth one more attempt and, if so, resumes it from its checkpoint.

Rules
-----
* Only jobs created by the current run are considered (ids not present before).
* Failures that would repeat are never retried: configuration, credentials,
  missing tools, content/quality gates and release blocks.
* A recovered job gets the same bookkeeping the normal path performs (duplicate
  ledger, topic queue, news "seen" cache), so a recovery cannot cause a rebuild.
* The number of attempts is bounded (``BUILD_RECOVERY_ATTEMPTS``, default 1).
"""
from __future__ import annotations

import os
import re

RESUMABLE_FORMATS = {"shorts", "news_short"}

# Exception class names stored as the prefix of ``videos.error``.
NON_RETRYABLE_TYPES = {
    "GeminiConfigError": "configuration",
    "GeminiCredentialError": "credentials",
    "FFmpegUnavailable": "missing tool",
    "VoiceProviderUnavailable": "no narration provider installed",
    "ReleaseBlocked": "release gate",
    "DuplicateUpload": "duplicate",
    "ChannelMismatchError": "wrong YouTube channel",
    "PublicPublishNotAuthorized": "public publish not authorised",
    "NoPublishableStory": "no publishable story",
    "ValueError": "invalid script or configuration",
    "KeyError": "invalid script or configuration",
    "TypeError": "programming error",
    "AttributeError": "programming error",
    "ImportError": "missing dependency",
    "ModuleNotFoundError": "missing dependency",
    "KeyboardInterrupt": "cancelled",
    "SystemExit": "stopped deliberately",
}
NON_RETRYABLE_TEXT = (
    (re.compile(r"video quality inspection failed", re.I), "quality gate"),
    (re.compile(r"failed content qa", re.I), "content gate"),
    (re.compile(r"not served for this api key|configuration problem|no usable script from any configured model",
                re.I), "configuration"),
    (re.compile(r"api[_ ]?key|credential|unauthori[sz]ed|permission denied|invalid_grant", re.I), "credentials"),
    (re.compile(r"ffmpeg (is )?(not|un)(available| installed| found)", re.I), "missing tool"),
)


def max_attempts() -> int:
    try:
        return max(0, min(3, int(os.getenv("BUILD_RECOVERY_ATTEMPTS", "1") or 1)))
    except ValueError:
        return 1


def classify_error(error) -> tuple[bool, str]:
    """``(retryable, reason)`` for an error string of the form ``Type: message``."""
    text = str(error or "").strip()
    if not text:
        return True, "no error recorded (interrupted)"
    kind = text.split(":", 1)[0].strip()
    if kind in NON_RETRYABLE_TYPES:
        return False, NON_RETRYABLE_TYPES[kind]
    for pattern, reason in NON_RETRYABLE_TEXT:
        if pattern.search(text):
            return False, reason
    return True, "possibly transient"


def job_ids() -> set[int]:
    from core import db
    return {int(row["id"]) for row in db.list_videos(limit=1000)}


def find_failed_job(before: set[int]) -> dict | None:
    """Newest job created by this run that did not finish."""
    from core import db
    rows = [dict(row) for row in db.list_videos(limit=1000) if int(row["id"]) not in before]
    rows = [row for row in rows if str(row.get("status")) in {"failed", "building"}]
    return max(rows, key=lambda row: int(row["id"])) if rows else None


def created_jobs(before: set[int]) -> list[dict]:
    from core import db
    return [dict(row) for row in db.list_videos(limit=1000) if int(row["id"]) not in before]


def can_resume(row: dict) -> tuple[bool, str]:
    from core.db import normalize_format
    fmt = normalize_format(row.get("format"))
    if fmt not in RESUMABLE_FORMATS:
        return False, f"format '{fmt}' is not resumed automatically"
    if not str(row.get("script_json") or "").strip():
        return False, "the job has no stored script"
    return classify_error(row.get("error"))


def resume(row: dict) -> str:
    """Rebuild ``row`` from its checkpoint and complete the normal bookkeeping."""
    import json
    from core import db
    from core.pipeline import build_shorts
    job_id = int(row["id"])
    script = json.loads(row.get("script_json") or "{}")
    language = row.get("language") or "english"
    db.update_video(job_id, attempts=int(row.get("attempts") or 0) + 1)
    out = build_shorts(script, job_id, language=language)
    finalise(db.get_video(job_id) or row)
    return str(out)


def finalise(row: dict) -> None:
    """Record a recovered job exactly as the normal build path would."""
    from core import dedupe
    from core.db import normalize_format
    fmt = normalize_format(row.get("format"))
    refs = [ref for ref in str(row.get("source_ref") or "").split(",") if ref.strip()]
    dedupe.Ledger().record(fmt, row.get("title") or "", key=str(row.get("topic_key") or ""),
                           source_refs=refs, job_id=row.get("id"), status="built")
    if fmt == "news_short" and refs:
        try:
            from core import config as cfg
            from core.daily_news_pipeline import mark_seen
            mark_seen(refs, cfg.STATE_DIR / "daily_news")
        except Exception as exc:
            print(f"WARNING: could not update the news seen cache: {exc}")
    if fmt == "shorts":
        try:
            from core import topics as topic_queue
            for item in topic_queue.load_topics():
                name = item.get("title") or item.get("topic") or ""
                if name and not item.get("done") and (
                        dedupe.topic_key(name) == row.get("topic_key")
                        or dedupe.is_same_subject(name, row.get("title") or "")):
                    topic_queue.update_topic(name, done=True)
                    break
        except Exception as exc:
            print(f"WARNING: could not update the topic queue: {exc}")
