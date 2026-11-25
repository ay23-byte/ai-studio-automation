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


def research_source_records(script: dict) -> list[dict]:
    """Read research sources from either supported script field.

    Older script generations store the same source records under ``sources``;
    newer generations use ``research_sources``. An empty newer field must not
    mask a populated legacy field.
    """
    if not isinstance(script, dict):
        return []
    records = script.get("research_sources")
    if isinstance(records, list) and records:
        return records
    records = script.get("sources")
    return records if isinstance(records, list) else []


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
    # Public publishers occasionally return transient 429/5xx responses or
    # close headless-browser connections. Retry those failures, but do not
    # retry permanent URL/security/content errors. A screenshot is accepted
    # only when the renderer produced a real page image and its sidecar.
    timeout_seconds = max(10, int(os.getenv("SOURCE_SCREENSHOT_TIMEOUT_SECONDS", "55")))
    attempts = max(1, min(3, int(os.getenv("SOURCE_SCREENSHOT_ATTEMPTS", "3"))))
    failures = []
    result = None
    for attempt in range(1, attempts + 1):
        image_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        try:
            result = subprocess.run(
                [node, str(script)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            if result.returncode == 0 and image_path.is_file() and image_path.stat().st_size >= 5000:
                break
            detail = (result.stderr or result.stdout or "screenshot renderer returned no image").strip()[-1200:]
        except subprocess.TimeoutExpired as exc:
            detail = f"TimeoutExpired after {timeout_seconds}s"
        except OSError as exc:
            detail = f"{type(exc).__name__}: {exc}"
        failures.append(f"attempt {attempt}/{attempts}: {detail}")
        transient = any(token in detail.lower() for token in (
            "timeout", "err_", "econn", "429", "502", "503", "504",
            "target closed", "browser has been closed", "temporarily unavailable",
            "navigation failed", "net::"
        ))
        if not transient or attempt >= attempts:
            break
        time.sleep(min(2 * attempt, 5))
    if (result is None or result.returncode != 0 or not image_path.is_file()
            or image_path.stat().st_size < 5000
            or not metadata_path.is_file()):
        return {
            "status": "FAILED",
            "reason": " | ".join(failures)[-2400:] or "Screenshot renderer did not produce a verified page image and metadata sidecar.",
            "source_id": source.get("source_id"),
            "url": url,
            "attempts": len(failures) if failures else 1,
        }
    # Add an editorial footer to the genuine page capture so viewers can read
    # the publisher, publication date and exact source URL in the finished film.
    try:
        from PIL import Image, ImageDraw, ImageFont
        image = Image.open(image_path).convert("RGB")
        canvas = image.copy()
        draw = ImageDraw.Draw(canvas)
        regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        try:
            title_font = ImageFont.truetype(bold, 23)
            url_font = ImageFont.truetype(regular, 16)
        except OSError:
            title_font, url_font = ImageFont.load_default(), ImageFont.load_default()
        publisher = str(source.get("publisher") or "Original source")
        published = str(source.get("published_at") or "Date not stated")
        footer_top = max(0, image.height - 112)
        draw.rectangle((0, footer_top, image.width, image.height), fill=(14, 18, 25))
        draw.text((28, footer_top + 12), f"SOURCE  |  {publisher}  |  {published}",
                  fill=(245, 247, 250), font=title_font)
        url_text = url
        max_width = image.width - 56
        while getattr(url_font, "size", 10) > 10 and draw.textbbox((0, 0), url_text, font=url_font)[2] > max_width:
            url_font = ImageFont.truetype(regular, max(10, url_font.size - 1))
        draw.text((28, footer_top + 58), url_text, fill=(170, 205, 240), font=url_font)
        canvas.save(image_path, format="PNG", optimize=True)
        sidecar = json.loads(metadata_path.read_text(encoding="utf-8"))
        sidecar["editorial_footer"] = {
            "publisher": publisher, "published_at": published, "source_url": url,
            "notice": "Footer labels the real webpage screenshot; it is not part of the publisher's original page.",
        }
        metadata_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return {"status": "FAILED", "reason": f"Could not label source screenshot: {type(exc).__name__}: {exc}"}
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
