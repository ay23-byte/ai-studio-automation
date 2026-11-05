"""Local, privacy-safe build telemetry and failure summaries.

No prompts, API keys, tokens, or user content are stored. Monetary cost is only
estimated when an operator explicitly configures a unit rate; otherwise it is
reported as unavailable rather than guessed.
"""
import json
import os
import re
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from core.config import OUTPUT_DIR, BUILD_DIR

LOG_PATH = OUTPUT_DIR / "monitoring" / "events.jsonl"


def record_event(component, action, status, duration_seconds=None, error=None, metadata=None):
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": str(component)[:60],
        "action": str(action)[:80],
        "status": str(status)[:30],
    }
    if duration_seconds is not None:
        event["duration_seconds"] = round(max(0.0, float(duration_seconds)), 3)
    if error:
        # Exception messages may contain provider response details; keep a short,
        # sanitized summary and never serialize request payloads or credentials.
        event["error_type"] = type(error).__name__ if isinstance(error, BaseException) else "Error"
        message = str(error)[:300].replace("\n", " ")
        message = re.sub(
            r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]", message,
        )
        message = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", message)
        event["error"] = message
    if metadata and isinstance(metadata, dict):
        safe = {}
        for key in ("job_id", "format", "provider", "model", "attempt", "output_bytes"):
            if key in metadata and isinstance(metadata[key], (str, int, float, bool)):
                safe[key] = metadata[key]
        if safe:
            event["metadata"] = safe
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events(limit=5000):
    if not LOG_PATH.is_file():
        return []
    events = []
    for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, int(limit)):]:
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                events.append(item)
        except (ValueError, TypeError):
            continue
    return events


def summarize_events(events=None):
    events = list(read_events() if events is None else events)
    statuses = Counter(str(e.get("status", "unknown")) for e in events)
    components = Counter(str(e.get("component", "unknown")) for e in events if e.get("status") in ("failure", "error"))
    durations = [float(e["duration_seconds"]) for e in events if isinstance(e.get("duration_seconds"), (int, float))]
    failures = [e for e in events if e.get("status") in ("failure", "error")]
    return {
        "total_events": len(events),
        "successes": statuses.get("success", 0),
        "failures": statuses.get("failure", 0) + statuses.get("error", 0),
        "warnings": statuses.get("warning", 0),
        "failure_components": dict(components),
        "average_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "recent_failures": failures[-20:][::-1],
        "events": events,
    }


def resource_snapshot():
    def folder_size(path):
        total = 0
        if path.exists():
            for item in path.rglob("*"):
                try:
                    if item.is_file():
                        total += item.stat().st_size
                except OSError:
                    pass
        return total
    usage = shutil.disk_usage(str(OUTPUT_DIR))
    return {
        "output_bytes": folder_size(OUTPUT_DIR),
        "build_bytes": folder_size(BUILD_DIR),
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
    }


def configured_cost_estimate():
    """Return optional transparent cost estimate only for configured per-unit rates."""
    rates = {
        "gemini_request": os.getenv("COST_PER_GEMINI_REQUEST"),
        "image_request": os.getenv("COST_PER_IMAGE_REQUEST"),
        "tts_minute": os.getenv("COST_PER_TTS_MINUTE"),
    }
    configured = {}
    for key, value in rates.items():
        try:
            if value not in (None, "") and float(value) >= 0:
                configured[key] = float(value)
        except (TypeError, ValueError):
            continue
    return {"available": False, "reason": "Provider billing and token usage are not exposed by this app; no monetary cost is guessed.", "configured_unit_rates": configured}


def monitored(component, action, fn):
    """Wrap a callable and persist success/failure plus elapsed time."""
    def wrapped(*args, **kwargs):
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            record_event(component, action, "success", time.monotonic() - started)
            return result
        except Exception as exc:
            record_event(component, action, "failure", time.monotonic() - started, error=exc)
            raise
    wrapped.__name__ = getattr(fn, "__name__", "monitored_call")
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    return wrapped
