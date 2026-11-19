"""Private-only publishing adapter around the existing, proven uploader.

This module contains no new OAuth implementation. It reuses ``core.uploader``
and refuses every path that is not private, verified and read back.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Protocol

from .artifacts import read_json, sha256_file, write_json
from .config import CHANNEL_HANDLE, PRIVACY_STATUS, V2Config
from .logging import redact
from .models import QualityReport, UploadResult


class UploaderAdapter(Protocol):
    def verify_channel(self) -> dict: ...
    def upload(self, video: Path, title: str, description: str, tags: list,
               *, privacy_status: str, disable_scheduling: bool, marker: str) -> str: ...
    def get_video_status(self, video_id: str) -> dict: ...


class ExistingUploaderAdapter:
    """Lazy wrapper; the protected uploader is imported only when publishing."""

    def verify_channel(self) -> dict:
        from core import uploader  # protected, existing implementation
        return uploader.verify_channel(force=True)

    def upload(self, video: Path, title: str, description: str, tags: list,
               *, privacy_status: str, disable_scheduling: bool, marker: str) -> str:
        from core import uploader
        return uploader.upload(video, title, description, tags, privacy_status=privacy_status,
                               disable_scheduling=disable_scheduling, marker=marker)

    def get_video_status(self, video_id: str) -> dict:
        from core import uploader
        return uploader.get_video_status(video_id)


class PrivatePublisher:
    def __init__(self, ledger_path: Path, config: V2Config):
        self.ledger_path = Path(ledger_path)
        self.config = config
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        value = read_json(self.ledger_path, [])
        return value if isinstance(value, list) else []

    def publish(self, *, video: Path, metadata: dict, quality: QualityReport,
                version: str = "v1", uploader: UploaderAdapter | None = None,
                content_hash: str = "") -> UploadResult:
        if PRIVACY_STATUS != "private":
            raise RuntimeError("v2 privacy constant must remain private")
        content_hash = content_hash or sha256_file(video)
        marker = f"PV2-CONTENT:{content_hash[:16]}:{version}"
        ledger = self._load()
        for record in ledger:
            if record.get("marker") == marker and record.get("video_id"):
                return UploadResult(state="UPLOADED_PRIVATE", video_id=record["video_id"],
                                    privacy="private", marker=marker, content_hash=content_hash,
                                    version=version, reused_existing_upload=True,
                                    evidence={"idempotent_reuse": True})
        if quality.upload_eligibility != "PASS":
            return UploadResult(state="QUALITY_REJECTED", marker=marker, content_hash=content_hash,
                                version=version, error="quality decision is not PASS",
                                evidence=quality.decision())
        if not self.config.upload_enabled:
            return UploadResult(state="SKIPPED", marker=marker, content_hash=content_hash,
                                version=version, error="PV2_UPLOAD is not enabled")
        if not Path(video).is_file():
            return UploadResult(state="UPLOAD_FAILED", marker=marker, content_hash=content_hash,
                                version=version, error="video file is missing")

        adapter = uploader or ExistingUploaderAdapter()
        try:
            channel = adapter.verify_channel()
            channel_id = channel.get("id") if isinstance(channel, dict) else None
            video_id = adapter.upload(
                Path(video), str(metadata.get("title", ""))[:100],
                str(metadata.get("description", ""))[:4900], list(metadata.get("tags") or []),
                privacy_status="private", disable_scheduling=True, marker=marker)
            stored = adapter.get_video_status(video_id)
            problems = []
            if not stored.get("found"):
                problems.append("YouTube did not return the uploaded video")
            if stored.get("privacy_status") != "private":
                problems.append(f"privacy read-back was {stored.get('privacy_status')!r}")
            if stored.get("publish_at"):
                problems.append("video has a scheduled publish time")
            if channel_id and stored.get("channel_id") and stored["channel_id"] != channel_id:
                problems.append("uploaded video is on the wrong channel")
            if problems:
                raise RuntimeError("; ".join(problems))
            result = UploadResult(
                state="UPLOADED_PRIVATE", video_id=video_id, privacy="private",
                uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), marker=marker,
                content_hash=content_hash, version=version,
                evidence={"readback": redact(stored), "channel_handle": CHANNEL_HANDLE})
        except Exception as exc:  # noqa: BLE001 - any upload error must be reported, never hidden
            result = UploadResult(state="UPLOAD_FAILED", marker=marker, content_hash=content_hash,
                                  version=version, error=redact(str(exc))[:1000])
        ledger.append(result.__dict__)
        write_json(self.ledger_path, ledger)
        write_json(self.ledger_path.with_name("last_upload_result.json"), result.__dict__)
        return result
