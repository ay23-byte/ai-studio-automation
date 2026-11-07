"""Persistent human editorial review for documentary shots.

Review approval is an editorial workflow state only. It does not certify factual
accuracy, historical authenticity, copyright ownership, licensing, or lip-sync.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REVIEW_STATUSES = {"pending", "approved", "rejected", "needs_replacement"}
SOURCE_REVIEW_STATUSES = {"pending", "reviewed", "needs_review"}
SCHEMA_VERSION = 1


class ReviewStoreError(ValueError):
    """Raised when persisted review data is malformed or incompatible."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_record(record: dict[str, Any], shot_id: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ReviewStoreError(f"Review record for {shot_id} must be an object.")
    status = record.get("status", "pending")
    source_status = record.get("source_review_status", "pending")
    if status not in REVIEW_STATUSES:
        raise ReviewStoreError(f"Invalid review status for {shot_id}: {status!r}.")
    if source_status not in SOURCE_REVIEW_STATUSES:
        raise ReviewStoreError(f"Invalid source review status for {shot_id}: {source_status!r}.")
    notes = record.get("reviewer_notes", "")
    reason = record.get("rejection_reason", "")
    if not isinstance(notes, str) or not isinstance(reason, str):
        raise ReviewStoreError(f"Notes and rejection reason for {shot_id} must be text.")
    replacement = record.get("replacement_requested", status == "needs_replacement")
    if not isinstance(replacement, bool):
        raise ReviewStoreError(f"replacement_requested for {shot_id} must be boolean.")
    reviewed_at = record.get("reviewed_at")
    if reviewed_at is not None and not isinstance(reviewed_at, str):
        raise ReviewStoreError(f"reviewed_at for {shot_id} must be a string or null.")
    return {
        "shot_id": shot_id,
        "chapter_id": str(record.get("chapter_id") or "unassigned"),
        "status": status,
        "reviewer_notes": notes,
        "rejection_reason": reason,
        "replacement_requested": replacement,
        "reviewed_at": reviewed_at,
        "source_review_status": source_status,
        "shot_fingerprint": str(record.get("shot_fingerprint") or ""),
    }


def _read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": None, "shots": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewStoreError(
            f"Cannot read editorial review file {path}: {exc}. "
            "Preserve the file, repair its JSON, and retry; it was not overwritten."
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ReviewStoreError(f"Unsupported or malformed editorial review schema in {path}.")
    records = payload.get("shots")
    if not isinstance(records, dict):
        raise ReviewStoreError(f"Editorial review file {path} has no valid 'shots' object.")
    clean = {}
    for shot_id, record in records.items():
        if not isinstance(shot_id, str) or not shot_id.strip():
            raise ReviewStoreError("Editorial review contains an empty shot ID.")
        clean[shot_id] = _validate_record(record, shot_id)
    return {"schema_version": SCHEMA_VERSION, "updated_at": payload.get("updated_at"), "shots": clean}


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def review_store_path(report_path: str | Path) -> Path:
    return Path(report_path).resolve().parent / "visual_review_decisions.json"


def _shot_fingerprint(shot: dict[str, Any], chapter: str) -> str:
    identity = {
        "chapter_id": chapter,
        "media_path": shot.get("media_path"),
        "visual_prompt": shot.get("visual_prompt"),
        "narration": shot.get("narration"),
        "media_type": shot.get("media_type"),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_reviews(report_path: str | Path, shots: list[dict] | None = None) -> dict[str, Any]:
    """Load persisted decisions, adding pending entries for new/changed shots."""
    path = review_store_path(report_path)
    store = _read_store(path)
    changed = False
    for index, shot in enumerate(shots or []):
        shot_id = str(shot.get("shot_id") or f"shot_{index + 1:04d}")
        chapter = str(shot.get("chapter_id") or shot.get("chapter") or shot.get("chapter_index") or "unassigned")
        fingerprint = _shot_fingerprint(shot, chapter)
        if shot_id not in store["shots"]:
            store["shots"][shot_id] = _validate_record({"chapter_id": chapter, "shot_fingerprint": fingerprint}, shot_id)
            changed = True
        else:
            record = store["shots"][shot_id]
            if record["shot_fingerprint"] and record["shot_fingerprint"] != fingerprint:
                # Never carry approval across changed narration, prompt, chapter, or media.
                record.update({
                    "chapter_id": chapter, "status": "pending",
                    "reviewer_notes": "Previous review invalidated because this shot's content changed.",
                    "rejection_reason": "", "replacement_requested": False,
                    "reviewed_at": None, "source_review_status": "pending",
                    "shot_fingerprint": fingerprint,
                })
                changed = True
            elif not record["shot_fingerprint"]:
                record["shot_fingerprint"] = fingerprint
                changed = True
            elif record["chapter_id"] == "unassigned" and chapter != "unassigned":
                record["chapter_id"] = chapter
                changed = True
    if changed:
        store["updated_at"] = _now()
        _atomic_write(path, store)
    return store


def save_review(report_path: str | Path, shot_id: str, *, status: str | None = None,
                chapter_id: str | None = None, reviewer_notes: str | None = None,
                rejection_reason: str | None = None, replacement_requested: bool | None = None,
                source_review_status: str | None = None) -> dict[str, Any]:
    """Update one shot without replacing other saved decisions."""
    if not isinstance(shot_id, str) or not shot_id.strip():
        raise ReviewStoreError("shot_id is required.")
    path = review_store_path(report_path)
    store = _read_store(path)
    current = store["shots"].get(shot_id, _validate_record({}, shot_id))
    updates = {
        "status": status,
        "chapter_id": chapter_id,
        "reviewer_notes": reviewer_notes,
        "rejection_reason": rejection_reason,
        "replacement_requested": replacement_requested,
        "source_review_status": source_review_status,
    }
    for key, value in updates.items():
        if value is not None:
            current[key] = value
    if current["status"] == "rejected" and not current["rejection_reason"].strip():
        raise ReviewStoreError("A rejection reason is required when rejecting a shot.")
    if current["status"] == "needs_replacement":
        current["replacement_requested"] = True
    elif status == "approved":
        current["replacement_requested"] = False
        current["rejection_reason"] = ""
    if any(value is not None for value in updates.values()):
        current["reviewed_at"] = _now()
    store["shots"][shot_id] = _validate_record(current, shot_id)
    store["updated_at"] = _now()
    _atomic_write(path, store)
    return store["shots"][shot_id]


def audit_visual_reviews(report: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    """Return an actionable release gate; no human review is inferred from metadata."""
    issues = []
    records = store.get("shots", {})
    for index, shot in enumerate(report.get("shots") or []):
        shot_id = str(shot.get("shot_id") or f"shot_{index + 1:04d}")
        record = records.get(shot_id)
        if record is None:
            issues.append({"level": "error", "shot_id": shot_id, "message": "No editorial decision exists; review this shot."})
            continue
        if record["status"] != "approved":
            issues.append({"level": "error", "shot_id": shot_id,
                           "message": f"Editorial status is '{record['status']}'; approve the shot before publication."})
        if record["replacement_requested"] or record["status"] == "needs_replacement":
            issues.append({"level": "error", "shot_id": shot_id, "message": "A replacement visual is still required."})
        if record["source_review_status"] != "reviewed":
            issues.append({"level": "error", "shot_id": shot_id,
                           "message": f"Source review status is '{record['source_review_status']}'; inspect provenance and rights separately."})
        if report.get("semantic_screening_enabled"):
            screening = shot.get("semantic_screening") or {}
            screening_status = screening.get("status", "not_run")
            if screening_status != "match":
                issues.append({
                    "level": "error", "shot_id": shot_id,
                    "message": (
                        f"AI visual-to-narration screening is '{screening_status}'; "
                        "resolve the screening concern and regenerate/review this shot before publication."
                    ),
                })
        visual_quality = shot.get("visual_quality") or {}
        if visual_quality.get("status") == "fail":
            messages = visual_quality.get("issues") or ["Resolve the technical visual-quality failure."]
            issues.append({
                "level": "error", "shot_id": shot_id,
                "message": "Visual quality check failed: " + "; ".join(str(item) for item in messages),
            })
        media_path = shot.get("media_path")
        if not media_path or not Path(media_path).is_file():
            issues.append({"level": "error", "shot_id": shot_id, "message": "Referenced media is missing; restore or replace it."})
    return {
        "shot_count": len(report.get("shots") or []),
        "approved_count": sum(1 for row in records.values() if row["status"] == "approved"),
        "pending_count": sum(1 for row in records.values() if row["status"] == "pending"),
        "rejected_count": sum(1 for row in records.values() if row["status"] == "rejected"),
        "replacement_count": sum(1 for row in records.values() if row["replacement_requested"]),
        "publication_ready": not issues,
        "issues": issues,
        "notice": ("Editorial and source review states are workflow attestations only. They do not establish factual accuracy, "
                   "historical authenticity, copyright ownership, legal licensing, or presenter lip-sync."),
    }


def assert_visual_review_ready(build_dir: str | Path) -> dict[str, Any]:
    """Raise ReviewStoreError unless every shot is editorially approved and source-reviewed."""
    root = Path(build_dir)
    report_path = root / "visual_review_report.json"
    if not report_path.is_file():
        raise ReviewStoreError(
            f"Visual review manifest is missing from {root}. Generate/recover the documentary review report first."
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewStoreError(f"Cannot read visual review manifest {report_path}: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("shots"), list):
        raise ReviewStoreError(f"Visual review manifest {report_path} has an invalid structure.")
    store = load_reviews(report_path, report["shots"])
    result = audit_visual_reviews(report, store)
    if not result["publication_ready"]:
        details = "; ".join(f"{item['shot_id']}: {item['message']}" for item in result["issues"])
        raise ReviewStoreError("Long-form publication is blocked by editorial review: " + details)
    from core.audio_quality import assert_audio_quality_ready
    result["audio_quality"] = assert_audio_quality_ready(root)
    return result
