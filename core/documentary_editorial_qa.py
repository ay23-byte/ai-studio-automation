"""Editorial QA for documentary graphics, captions-on-screen text and shot pacing.

These checks catch production defects that ordinary codec/resolution QA cannot:
search-query placeholders, clipped title copy, unsourced/generic charts and
overlong static beats. They are conservative diagnostics, not fact-checking.
"""
from __future__ import annotations

import re
from collections import Counter


_PLACEHOLDER = re.compile(
    r"\b(?:FOOTAGE|STOCK SEARCH|B-ROLL|INSERT FOOTAGE|"
    r"SUN SURFACE ACTIVITY|POWER SUBSTATION EQUIP|"
    r"VISUALI[ZS]ATION|ELECTRICAL TRANSFORMER)\b", re.I
)


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item is not None).strip()
    return str(value or "").strip()


def _chart_has_real_payload(spec) -> bool:
    if not isinstance(spec, dict):
        return False
    labels = spec.get("labels") or spec.get("categories") or spec.get("x")
    values = spec.get("values") or spec.get("data") or spec.get("series")
    if not isinstance(labels, (list, tuple)) or not isinstance(values, (list, tuple)):
        return False
    return bool(labels and values and len(labels) == len(values))


def audit_editorial_shots(shots: list[dict], *, max_title_chars: int = 48,
                          max_static_seconds: float = 10.0) -> dict:
    """Return per-shot editorial issues without inventing facts or source evidence."""
    issues = []
    chart_fingerprints = Counter()
    chapter_labels = []
    for index, shot in enumerate(shots or [], start=1):
        shot_id = f"shot_{index:04d}"
        kind = str(shot.get("visual_type") or "").strip().lower()
        display = _text(shot.get("on_screen_text") or shot.get("title"))
        prompt = _text(shot.get("visual_prompt") or shot.get("image"))
        duration = float(shot.get("_dur") or 0.0)

        if display and _PLACEHOLDER.search(display):
            issues.append({"level": "error", "shot": shot_id,
                           "code": "search_placeholder_on_screen",
                           "message": "On-screen text contains footage-search or stock-placeholder wording."})
        if display and (len(display) > max_title_chars or len(display.splitlines()) > 2):
            issues.append({"level": "error", "shot": shot_id,
                           "code": "display_text_overflow",
                           "message": f"On-screen text is too long for a clean title ({len(display)} characters); shorten or reflow it."})
        if kind in {"chart", "data_chart", "graph"}:
            spec = shot.get("chart_spec")
            if not _chart_has_real_payload(spec):
                issues.append({"level": "error", "shot": shot_id,
                               "code": "chart_without_data",
                               "message": "Chart has no structured labels and values; replace it with a sourced diagram or real data."})
            elif isinstance(spec, dict):
                import json
                chart_fingerprints[json.dumps(spec, sort_keys=True, default=str)] += 1
        if kind in {"chapter_title", "chapter_card"}:
            label = display or _text(shot.get("chapter_title") or shot.get("chapter"))
            chapter_labels.append((shot_id, label))
            if duration > 3.5:
                issues.append({"level": "warning", "shot": shot_id,
                               "code": "chapter_card_too_long",
                               "message": "Chapter title holds longer than 3.5 seconds; use a short title over relevant moving footage."})
            if not prompt and not shot.get("background_media"):
                issues.append({"level": "warning", "shot": shot_id,
                               "code": "chapter_card_background_unspecified",
                               "message": "Chapter title has no described footage or visual background."})
        if duration > max_static_seconds and kind in {"chart", "diagram", "map", "still", "image"}:
            issues.append({"level": "warning", "shot": shot_id,
                           "code": "static_visual_held_too_long",
                           "message": f"Static visual is held for {duration:.1f}s; add motion, detail progression or a cutaway."})

    repeated_charts = [fingerprint for fingerprint, count in chart_fingerprints.items() if count > 1]
    if repeated_charts:
        issues.append({"level": "error", "shot": "documentary",
                       "code": "repeated_identical_charts",
                       "message": f"{len(repeated_charts)} identical chart specification(s) are reused; each chart must explain its own section."})
    return {
        "status": "FAIL" if any(item["level"] == "error" for item in issues) else (
            "REVIEW" if issues else "PASS"),
        "issue_count": len(issues),
        "error_count": sum(item["level"] == "error" for item in issues),
        "warning_count": sum(item["level"] == "warning" for item in issues),
        "issues": issues,
        "chapter_title_count": len(chapter_labels),
        "notice": "Editorial QA detects layout and placeholder risks; a human must still inspect the rendered frames.",
    }
