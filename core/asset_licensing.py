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

# The first thirteen columns are the original register; release packaging and
# review tools read them by name. The columns after "notes" were added with the
# multi-source footage engine and record what each licence permits and which
# exact file was used. "unknown" means the source did not say.
CSV_FIELDS = [
    "shot_id", "source", "url", "author", "licence", "attribution_text",
    "date_accessed", "licence_url", "score_0_10", "use_status",
    "human_review_required", "fallback_required", "notes",
    "provider_asset_id", "title", "media_type", "licence_state",
    "commercial_use_allowed", "modification_allowed", "attribution_required", "share_alike",
    "download_url", "local_file", "media_sha256", "width", "height", "duration_seconds",
    "file_validated",
]

def _normal(value: object) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def licence_is_usable(metadata: dict) -> tuple[bool, str]:
    """Accept only a known licence; provider name alone is never sufficient.

    The decision comes from ``core.footage_licence.classify_licence``, the one
    rule set shared by footage discovery, download and this register.
    """
    from core.footage_licence import classify_licence
    decision = classify_licence(metadata)
    return bool(decision["publishable"]), str(decision["reason"])


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
    rows, blocked, credits, courtesy = [], [], [], []
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
        is_project_graphic = (
            asset_type == "programmatic_graphic"
            and metadata.get("ai_generated") is False
            and provider == "LabLens/AI Studio local graphic renderer"
            and bool(metadata.get("font_licence"))
            and bool(metadata.get("font_licence_url"))
        )
        from core.footage_licence import attribution_line, classify_licence, provenance_gaps, tri_state
        decision = classify_licence(metadata)
        licensed, licence_status = bool(decision["publishable"]), str(decision["reason"])
        if decision.get("notes"):
            licence_status += "; " + "; ".join(decision["notes"])
        if is_ai:
            licensed = False
            licence_status = "AI-generated; not a real-world source; provider terms require separate review"
        elif is_project_graphic:
            licensed = True
            licence_status = "project_created_original_graphic; DejaVu font licence recorded; human review required"
        url = str(metadata.get("page_url") or metadata.get("url") or "")
        author = str(metadata.get("creator") or metadata.get("author") or metadata.get("user") or "")
        licence = str(metadata.get("licence") or metadata.get("license") or
                      ("Project-created original graphic" if is_project_graphic else ""))
        licence_url = str(metadata.get("licence_url") or metadata.get("license_url") or "")
        attribution = str(metadata.get("attribution_text") or "")
        if is_project_graphic and not attribution:
            attribution = (
                "Original graphic created by the AI Studio Automation project. "
                + str(metadata.get("font_attribution") or
                      "Font attribution details must be reviewed before publication.")
            )
        if licensed and not attribution:
            named = dict(metadata)
            named.setdefault("title", path.name if path else "Untitled")
            attribution = attribution_line(named, decision)
        quality = metadata.get("quality_score")
        if quality is None:
            quality = score_asset(metadata, int(metadata.get("width") or 0), int(metadata.get("height") or 0))["score_0_10"]
        fallback = not (path and path.is_file()) or (not licensed and not is_ai)
        note = licence_status
        if not is_project_graphic:
            for gap in provenance_gaps(metadata, decision):
                note += "; " + gap
        if fallback:
            blocked.append({"shot_id": shot_id, "source": provider, "reason": note,
                            "replacement": "Request a licensed asset or route to clearly disclosed AI_IMAGE/AI_VIDEO replacement."})
        if licensed and not is_ai and (url and licence_url or is_project_graphic):
            # A credit the licence requires must appear; the rest are listed as sources.
            (credits if decision.get("attribution_required") or is_project_graphic else courtesy).append(attribution)
        validation = metadata.get("validation") if isinstance(metadata.get("validation"), dict) else {}
        rows.append({
            "provider_asset_id": str(metadata.get("asset_id") or ""),
            "title": str(metadata.get("title") or ""),
            "media_type": str(metadata.get("media_type") or media.get("type") or ""),
            "licence_state": ("ai_generated" if is_ai else
                              ("project_created" if is_project_graphic else decision["state"])),
            "commercial_use_allowed": "unknown" if is_ai else ("yes" if is_project_graphic else tri_state(decision["commercial_use"])),
            "modification_allowed": "unknown" if is_ai else ("yes" if is_project_graphic else tri_state(decision["modification"])),
            "attribution_required": "unknown" if is_ai else tri_state(decision["attribution_required"]),
            "share_alike": "unknown" if is_ai else tri_state(decision["share_alike"]),
            "download_url": str(metadata.get("download_url") or ""),
            "local_file": str(path) if path else "",
            "media_sha256": str(metadata.get("media_sha256") or ""),
            "width": metadata.get("width") or "", "height": metadata.get("height") or "",
            "duration_seconds": metadata.get("duration_seconds") or "",
            "file_validated": "yes" if validation.get("ok") else "not_recorded",
            "shot_id": shot_id, "source": provider, "url": url, "author": author,
            "licence": licence or ("AI-generated" if is_ai else "UNKNOWN"),
            "attribution_text": attribution, "date_accessed": str(metadata.get("date_accessed") or date.today().isoformat()),
            "licence_url": licence_url, "score_0_10": quality,
            "use_status": ("ai_reconstruction_disclosure_and_terms_review" if is_ai else ("eligible_pending_human_review" if licensed and ((url and licence_url) or is_project_graphic) else "blocked")),
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
    unique_courtesy = [line for line in dict.fromkeys(courtesy) if line not in unique_credits]
    if unique_credits or unique_courtesy:
        body = "\n".join(unique_credits + unique_courtesy)
    else:
        body = "No externally sourced assets currently have complete, verified licence and attribution metadata."
    credits_path.write_text(
        "DOCUMENTARY SOURCE CREDITS\n\n" + body +
        "\n\nHuman rights and context review is required before publication.\n",
        encoding="utf-8",
    )
    return {
        "csv_path": str(csv_path), "credits_path": str(credits_path),
        "asset_count": len(rows), "eligible_pending_human_review": sum(
            1 for row in rows if row["use_status"] == "eligible_pending_human_review"
        ),
        "blocked_count": len(blocked), "blocked_assets": blocked,
        "credits_required_by_licence": len(unique_credits),
        "sources_listed_without_obligation": len(unique_courtesy),
        "licence_states": {state: sum(1 for row in rows if row["licence_state"] == state)
                           for state in sorted({row["licence_state"] for row in rows})},
        "publication_ready": False,
        "notice": "Scores are metadata-only screening. Human inspection must confirm exact visual match, clean/stable frames, watermarks, privacy, context, licence terms and attribution before publication.",
    }
