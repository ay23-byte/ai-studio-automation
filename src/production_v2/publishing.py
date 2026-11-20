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
    def find_private_video_by_marker(self, marker: str) -> str | None: ...


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

    def find_private_video_by_marker(self, marker: str) -> str | None:
        from core import uploader
        return uploader.find_private_video_by_marker(marker)


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
        if not Path(video).is_file():
            return UploadResult(state="UPLOAD_FAILED", error="video file is missing")
        content_hash = content_hash or sha256_file(video)
        marker = f"PV2-CONTENT:{content_hash[:16]}:{version}"
        ledger = self._load()
        if quality.upload_eligibility != "PASS":
            return UploadResult(state="QUALITY_REJECTED", marker=marker, content_hash=content_hash,
                                version=version, error="quality decision is not PASS",
                                evidence=quality.decision())
        if not self.config.upload_enabled:
            return UploadResult(state="SKIPPED", marker=marker, content_hash=content_hash,
                                version=version, error="PV2_UPLOAD is not enabled")

        adapter = uploader or ExistingUploaderAdapter()
        video_id = ""
        reused = False
        try:
            channel = adapter.verify_channel()
            if not isinstance(channel, dict) or not channel.get("verified") or not channel.get("id"):
                raise RuntimeError("authenticated channel could not be verified")
            channel_id = channel["id"]
            handle = str(channel.get("handle") or "").lower().lstrip("@")
            if handle and handle != CHANNEL_HANDLE.lower().lstrip("@"):
                raise RuntimeError("authenticated channel handle does not match the target")
            # A previous attempt may have created a video even when read-back failed.
            previous = next((r for r in reversed(ledger)
                             if r.get("marker") == marker and r.get("video_id")), None)
            if previous:
                video_id = previous["video_id"]
                reused = True
            else:
                video_id = adapter.find_private_video_by_marker(marker) or ""
                reused = bool(video_id)
                if not video_id:
                    # Persist intent before the network call. An interrupted/ambiguous
                    # request must be reconciled manually rather than retried blindly.
                    if any(r.get("marker") == marker and r.get("state") == "UPLOAD_PENDING" for r in ledger):
                        raise RuntimeError("previous upload outcome is unknown; reconcile the marker before retrying")
                    ledger.append({"marker": marker, "state": "UPLOAD_PENDING"})
                    write_json(self.ledger_path, ledger)
                    video_id = adapter.upload(
                        Path(video), str(metadata.get("title", ""))[:100],
                        str(metadata.get("description", ""))[:4900], list(metadata.get("tags") or []),
                        privacy_status="private", disable_scheduling=True, marker=marker)
                    if not video_id:
                        raise RuntimeError("upload did not return a video ID")
                    # Record the ID before read-back so failed verification cannot
                    # cause a second upload on retry.
                    ledger.append({"marker": marker, "state": "UPLOAD_UNVERIFIED", "video_id": video_id})
                    write_json(self.ledger_path, ledger)
            stored = adapter.get_video_status(video_id)
            problems = []
            if not stored.get("found"):
                problems.append("YouTube did not return the uploaded video")
            if stored.get("privacy_status") != "private":
                problems.append(f"privacy read-back was {stored.get('privacy_status')!r}")
            if stored.get("publish_at"):
                problems.append("video has a scheduled publish time")
            if stored.get("channel_id") != channel_id:
                problems.append("uploaded video channel is missing or does not match the verified channel")
            if problems:
                raise RuntimeError("; ".join(problems))
            result = UploadResult(
                state="UPLOADED_PRIVATE", video_id=video_id, privacy="private",
                uploaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), marker=marker,
                content_hash=content_hash, version=version, reused_existing_upload=reused,
                evidence={"readback": redact(stored), "channel_handle": CHANNEL_HANDLE})
        except Exception as exc:  # Any upload error must be reported, never hidden.
            result = UploadResult(state="UPLOAD_FAILED", video_id=video_id, marker=marker,
                                  content_hash=content_hash, version=version,
                                  error=redact(str(exc))[:1000])
        ledger.append(result.__dict__)
        write_json(self.ledger_path, ledger)
        write_json(self.ledger_path.with_name("last_upload_result.json"), result.__dict__)
        return result
