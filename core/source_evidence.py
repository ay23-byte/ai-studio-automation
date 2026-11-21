"""Capture genuine source-page screenshots for evidence-led documentary shots.

Only research records explicitly cited by a chart, timeline or document beat are
eligible. A screenshot is an editorial illustration of the source page, not proof
that the claim is true; the claim still needs source verification.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


EVIDENCE_TYPES = {"chart", "timeline", "document", "evidence"}


def cited_source_records(beat: dict, research_sources: list[dict]) -> list[dict]:
    """Resolve a beat's explicit source IDs to valid, titled HTTP(S) source records."""
    refs = list(beat.get("source_refs") or [])
    chart = beat.get("chart_spec") or {}
    refs.extend(chart.get("source_refs") or [])
    for event in beat.get("timeline_events") or []:
        refs.extend(event.get("source_refs") or [])
    wanted = list(dict.fromkeys(str(ref).strip() for ref in refs if str(ref).strip()))
    by_id = {
        str(row.get("source_id") or row.get("id") or "").strip(): row
        for row in research_sources or []
        if isinstance(row, dict)
    }
    records = []
    for source_id in wanted:
        row = by_id.get(source_id)
        if not row:
            continue
        url = str(row.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if parsed.username or parsed.password:
            continue
        if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            continue
        records.append({
            "source_id": source_id,
            "title": str(row.get("title") or "Original source").strip(),
            "publisher": str(row.get("publisher") or parsed.hostname).strip(),
            "published_at": str(row.get("published_at") or row.get("publication_date") or "Date not stated").strip(),
            "url": url,
        })
    return records


def capture_source_screenshot(source: dict, output_dir: str | Path, *, node: str = "node") -> dict:
    """Capture the visible, real source page using the repository's pinned Playwright."""
    url = str(source.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {"status": "SKIPPED", "reason": "invalid source URL"}
    root = Path(__file__).resolve().parents[1]
    script = root / "avatar_studio" / "capture-source.mjs"
    if not script.is_file():
        return {"status": "SKIPPED", "reason": "pinned Playwright capture script is missing"}
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(source.get("source_id") or "source"))
    image_path = destination / f"{safe_id}.png"
    metadata_path = image_path.with_suffix(".png.source.json")
    payload = {
        **source,
        "image_path": str(image_path.resolve()),
        "metadata_path": str(metadata_path.resolve()),
    }
    try:
        result = subprocess.run(
            [node, str(script)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=int(os.getenv("SOURCE_SCREENSHOT_TIMEOUT_SECONDS", "55")),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0 or not image_path.is_file() or image_path.stat().st_size < 5000:
        detail = (result.stderr or result.stdout or "screenshot renderer returned no image").strip()[-1200:]
        return {"status": "FAILED", "reason": detail}
    return {
        "status": "CAPTURED",
        "path": str(image_path),
        "metadata_path": str(metadata_path),
        "source_id": source.get("source_id"),
        "title": source.get("title"),
        "publisher": source.get("publisher"),
        "published_at": source.get("published_at"),
        "url": url,
    }
