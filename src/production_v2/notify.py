"""Failure notifications using only the Python standard library.

Earlier production failures were caused by a notification script importing an
optional package.  This implementation never imports requests.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .config import V2Config
from .logging import redact


def build_payload(state: str, report: dict) -> dict:
    return redact({
        "source": "engineering-physics-production-v2",
        "state": state,
        "run_id": report.get("run_id", "unknown"),
        "content_type": report.get("content_type", "unknown"),
        "error": str(report.get("error", ""))[:800],
        "artifact_dir": str(report.get("artifact_dir", ""))[:300],
    })


def send_failure_notification(state: str, report: dict, config: V2Config,
                              transport: Callable[[str, bytes], None] | None = None) -> bool:
    """Return True when a notification was sent; never raise on delivery failure."""
    url = config.failure_webhook or os.environ.get("PV2_FAILURE_WEBHOOK", "")
    if not url:
        return False
    payload = json.dumps(build_payload(state, report), ensure_ascii=False).encode("utf-8")
    try:
        if transport is not None:
            transport(url, payload)
        else:
            request = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
                response.read(1024)
        return True
    except (OSError, urllib.error.URLError, ValueError):
        return False
