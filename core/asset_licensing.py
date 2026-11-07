"""Asset provenance, conservative licence gating and per-build credits.

This module never interprets a search result as proof of rights. Only explicit,
recognized licence labels enter the verified-use list; all other sources remain
blocked for publication pending human rights review.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

CSV_FIELDS = [
    "shot_id", "source", "url", "author", "licence", "attribution_text",
    "date_accessed", "licence_url", "score_0_10", "use_status",
    "human_review_required", "fallback_required", "notes",
]

PUBLIC_DOMAIN = {"public domain", "cc0", "cc0 1.0", "creative commons zero"}
CC_BY = {"cc by", "cc by 2.0", "cc by 3.0", "cc by 4.0",
         "cc by-sa", "cc by-sa 2.0", "cc by-sa 3.0", "cc by-sa 4.0"}
STOCK = {"pixabay content license", "pexels license"}


def _normal(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def licence_is_usable(metadata: dict) -> tuple[bool, str]:
    """Accept only a known licence; provider name alone is never sufficient."""
    label = _normal(metadata.get("licence") or metadata.get("license"))
    if label in PUBLIC_DOMAIN or label in CC_BY:
        return True, "verified_label_requires_human_file_page_check"
    if label in STOCK:
        return True, "stock_terms_apply_human_restrictions_check_required"
    return False, "unclear_or_unrecognized_licence"


def score_asset(metadata: dict, width: int = 0, height: int = 0) -> dict:
    """Conservative 0-10 score; unknown visual properties are not guessed."""
    relevance = metadata.get("relevance_score")
    try:
        match = max(0.0, min(10.0, float(relevance) * 10.0))
    except (TypeError, ValueError):
        match = 0.0
    resolution = 2.0 if width >= 1920 and height >= 1080 else (
        1.0 if width >= 1280 and height >= 720 else 0.0
    )
    licensed, _ = licence_is_usable(metadata)
    licence = 2.0 if licensed and metadata.get("licence_url") else 0.0
    # Stability, watermark/logo and identifiable-private-person checks require
    # frame inspection. Do not award points from metadata alone.
    score = round(min(10.0, match * 0.5 + resolution + licence), 2)
    return {
        "score_0_10": score,
        "literal_match_points": round(match * 0.5, 2),
        "resolution_points": resolution,
        "licence_clarity_points": licence,
        "steady_clean_points": None,
        "watermark_logo_points": None,
        "privacy_points": None,
        "human_visual_inspection_required": True,
    }


def build_licence_register(shot_timeline: list[dict], build_dir: str | Path) -> dict:
    """Write licenses.csv and a human-readable end-credit/description text file."""
    root = Path(build_dir)
    root.mkdir(parents=True, exist_ok=True)
    rows, blocked, credits = [], [], []
    seen_recent: list[tuple[float, str]] = []
    for index, shot in enumerate(shot_timeline, start=1):
        shot_id = f"shot_{index:04d}"
        media = shot.get("_media") or {}
        raw_path = media.get("path")
        path = Path(raw_path) if raw_path else None
        sidecar = path.with_suffix(path.suffix + ".source.json") if path else None
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar and sidecar.is_file() else {}
        except (OSError, ValueError):
            metadata = {}
        provider = str(metadata.get("provider") or "Unknown source")
        asset_type = str(metadata.get("asset_type") or media.get("type") or "unknown")
        is_ai = "ai_generated" in asset_type.lower()
        licensed, licence_status = licence_is_usable(metadata)
        if is_ai:
            licensed = False
            licence_status = "AI-generated; not a real-world source; provider terms require separate review"
        url = str(metadata.get("page_url") or metadata.get("url") or "")
        author = str(metadata.get("creator") or metadata.get("author") or metadata.get("user") or "")
        licence = str(metadata.get("licence") or metadata.get("license") or "")
        licence_url = str(metadata.get("licence_url") or metadata.get("license_url") or "")
        attribution = str(metadata.get("attribution_text") or "")
        if licensed and not attribution:
            if _normal(licence).startswith("cc by"):
                attribution = f"{author or 'Author not supplied'}, {metadata.get('title') or path.name if path else 'Untitled'}, {licence}, {url}"
            else:
                attribution = f"{metadata.get('title') or path.name if path else 'Untitled'} — {provider} ({licence}); source: {url}"
        quality = metadata.get("quality_score")
        if quality is None:
            quality = score_asset(metadata, int(metadata.get("width") or 0), int(metadata.get("height") or 0))["score_0_10"]
        fallback = not (path and path.is_file()) or not licensed
        note = licence_status
        if not url:
            note += "; source page URL missing"
        if not licence_url:
            note += "; licence URL missing"
        if fallback:
            blocked.append({"shot_id": shot_id, "source": provider, "reason": note,
                            "replacement": "Request a licensed asset or route to clearly disclosed AI_IMAGE/AI_VIDEO replacement."})
        if licensed and url and licence_url and not is_ai:
            credits.append(attribution)
        rows.append({
            "shot_id": shot_id, "source": provider, "url": url, "author": author,
            "licence": licence or ("AI-generated" if is_ai else "UNKNOWN"),
            "attribution_text": attribution, "date_accessed": str(metadata.get("date_accessed") or date.today().isoformat()),
            "licence_url": licence_url, "score_0_10": quality,
            "use_status": "eligible_pending_human_review" if licensed and url and licence_url else "blocked",
            "human_review_required": True, "fallback_required": fallback,
            "notes": note,
        })
        if path and path.is_file() and not is_ai:
            try:
                start = float(shot.get("_start") or 0)
                for old_start, old_key in seen_recent:
                    if abs(start - old_start) < 90 and old_key == str(metadata.get("asset_id") or url or path):
                        rows[-1]["notes"] += "; duplicate asset reused within 90 seconds"
                        rows[-1]["use_status"] = "blocked"
                        rows[-1]["fallback_required"] = True
                        rows[-1]["notes"] += "; replace with a different asset"
                        blocked.append({"shot_id": shot_id, "source": provider,
                                        "reason": "same asset reused within 90 seconds"})
                seen_recent.append((start, str(metadata.get("asset_id") or url or path)))
            except (TypeError, ValueError):
                pass

    csv_path = root / "licenses.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    credits_path = root / "credits.txt"
    unique_credits = list(dict.fromkeys(credits))
    credits_path.write_text(
        "DOCUMENTARY SOURCE CREDITS\n\n" +
        ("\n".join(unique_credits) if unique_credits else
         "No externally sourced assets currently have complete, verified licence and attribution metadata.") +
        "\n\nHuman rights and context review is required before publication.\n",
        encoding="utf-8",
    )
    return {
        "csv_path": str(csv_path), "credits_path": str(credits_path),
        "asset_count": len(rows), "eligible_pending_human_review": sum(
            1 for row in rows if row["use_status"] == "eligible_pending_human_review"
        ),
        "blocked_count": len(blocked), "blocked_assets": blocked,
        "publication_ready": False,
        "notice": "Scores are metadata-only screening. Human inspection must confirm exact visual match, clean/stable frames, watermarks, privacy, context, licence terms and attribution before publication.",
    }
