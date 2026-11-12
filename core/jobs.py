"""Job orchestration: stage checkpoints, resume, and failure bookkeeping.

A render is a sequence of expensive stages (script → voice → visuals → render →
QA → thumbnail → upload). Previously any failure discarded all completed work:
a provider outage on the last visual of a 100-shot documentary meant paying for
the script, narration and every earlier download again on the next attempt.

``Checkpoint`` persists each completed stage in ``build/job_<id>/checkpoint.json``
(and mirrors it to the ``job_stages`` table). Re-running the same job id skips
stages whose inputs are unchanged *and* whose output files still exist.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CHECKPOINT_NAME = "checkpoint.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_hash(*parts: Any) -> str:
    """Deterministic fingerprint of JSON-serialisable stage inputs."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(json.dumps(part, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:32]


def resume_enabled() -> bool:
    return os.getenv("AI_STUDIO_RESUME", "1").strip().lower() not in {"0", "false", "no", "off"}


class Checkpoint:
    """Per-job stage ledger stored next to the build artefacts."""

    def __init__(self, job_id, build_dir, mirror_to_db: bool = True):
        self.job_id = job_id
        self.build_dir = Path(build_dir)
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.build_dir / CHECKPOINT_NAME
        self.mirror_to_db = mirror_to_db
        self.data = {"job_id": job_id, "created_at": _now(), "stages": {}}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("stages"), dict):
                    self.data = loaded
            except (OSError, ValueError):
                # A truncated checkpoint only costs a rebuild; keep the bad file
                # for inspection instead of deleting it.
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass

    # ------------------------------------------------------------ persistence
    def _save(self) -> None:
        self.data["updated_at"] = _now()
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _mirror(self, stage: str, record: dict) -> None:
        if not self.mirror_to_db or self.job_id is None:
            return
        try:
            from core.db import record_stage
            record_stage(
                self.job_id, stage, record.get("status", "unknown"),
                attempt=record.get("attempt", 1), started_at=record.get("started_at"),
                finished_at=record.get("finished_at"),
                duration_seconds=record.get("duration_seconds"),
                input_hash=record.get("input_hash"), detail=record.get("result"),
                error=record.get("error"),
            )
        except Exception as exc:  # bookkeeping must never fail a render
            print(f"WARNING: could not mirror stage '{stage}' to the database: {exc}")

    # ------------------------------------------------------------------ query
    def get(self, stage: str) -> dict | None:
        return self.data["stages"].get(stage)

    def is_done(self, stage: str, input_hash: str | None = None, files=()) -> bool:
        record = self.get(stage)
        if not record or record.get("status") != "done":
            return False
        if input_hash is not None and record.get("input_hash") != input_hash:
            return False
        for name in list(files or ()) + list(record.get("files") or ()):
            path = Path(name)
            if not path.is_absolute():
                path = self.build_dir / path
            try:
                if not path.is_file() or path.stat().st_size == 0:
                    return False
            except OSError:
                return False
        return True

    def summary(self) -> list[dict]:
        return [
            {"stage": name, **{k: v for k, v in record.items() if k != "result"}}
            for name, record in self.data["stages"].items()
        ]

    # ----------------------------------------------------------------- update
    def mark(self, stage: str, status: str, *, input_hash=None, result=None,
             files=(), error=None, started_at=None, duration_seconds=None) -> dict:
        previous = self.get(stage) or {}
        previous_attempt = int(previous.get("attempt", 0) or 0)
        attempt = previous_attempt + 1 if status == "running" else max(1, previous_attempt)
        record = {
            "status": status,
            "attempt": attempt,
            "started_at": started_at or previous.get("started_at") or _now(),
            "finished_at": _now() if status in {"done", "failed", "skipped"} else None,
            "duration_seconds": duration_seconds,
            "input_hash": input_hash if input_hash is not None else previous.get("input_hash"),
            "files": [str(item) for item in files] if files else previous.get("files", []),
        }
        if result is not None:
            record["result"] = result
        elif status == "done" and "result" in previous:
            record["result"] = previous["result"]
        if error:
            record["error"] = str(error)[:1000]
        self.data["stages"][stage] = record
        self._save()
        self._mirror(stage, record)
        return record

    def run(self, stage: str, fn: Callable[[], Any], *, input_hash: str | None = None,
            files=(), to_result: Callable[[Any], Any] | None = None,
            from_result: Callable[[Any], Any] | None = None) -> Any:
        """Run ``fn`` unless an identical, intact checkpoint already exists.

        ``to_result`` converts the return value into a JSON-safe record;
        ``from_result`` rebuilds the return value from that record on resume.
        """
        if resume_enabled() and self.is_done(stage, input_hash, files):
            record = self.get(stage)
            print(f"[checkpoint] job {self.job_id}: reusing completed stage '{stage}'.")
            stored = record.get("result")
            return from_result(stored) if from_result else stored
        started_at, started = _now(), time.monotonic()
        self.mark(stage, "running", input_hash=input_hash, started_at=started_at)
        try:
            value = fn()
        except BaseException as exc:
            self.mark(stage, "failed", input_hash=input_hash, error=f"{type(exc).__name__}: {exc}",
                      started_at=started_at, duration_seconds=round(time.monotonic() - started, 3))
            raise
        stored = to_result(value) if to_result else value
        try:
            json.dumps(stored, default=str)
        except (TypeError, ValueError):
            stored = None
        self.mark(stage, "done", input_hash=input_hash, result=stored, files=files,
                  started_at=started_at, duration_seconds=round(time.monotonic() - started, 3))
        return value


def load_checkpoint_summary(build_dir) -> list[dict]:
    """Read-only helper for the dashboard and CLI."""
    path = Path(build_dir) / CHECKPOINT_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    stages = data.get("stages") if isinstance(data, dict) else None
    if not isinstance(stages, dict):
        return []
    return [{"stage": name, **{k: v for k, v in record.items() if k != "result"}}
            for name, record in stages.items() if isinstance(record, dict)]
