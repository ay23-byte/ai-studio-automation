"""Narration-locked documentary Edit Decision List planning and QA.

The EDL is an editorial plan/report. It does not claim word-level synchronization
unless word timestamps are supplied, and it never treats metadata matching as proof
that footage depicts the narrated fact.
"""
from __future__ import annotations

import re
from pathlib import Path


def _pacing_profile(tone: str) -> tuple[str, float, float]:
    value = str(tone or "investigative").strip().lower()
    if any(token in value for token in ("business", "explainer", "tech", "sports")):
        return "fast", 3.0, 5.0
    if any(token in value for token in ("nature", "emotional", "biography")):
        return "slow", 8.0, 14.0
    return "medium", 5.0, 8.0


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?।])\s+", str(text or "")) if part.strip()]


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _asset_id(shot: dict, index: int) -> str:
    media = shot.get("_media") or {}
    path = media.get("path")
    return Path(str(path)).name if path else f"missing_scene_{index:04d}"


def _visual_motion(shot: dict) -> str:
    kind = str(shot.get("visual_type") or "").lower()
    movement = str(shot.get("camera_movement") or "").lower()
    if "pan left" in movement or "pan_left" in movement:
        return "pan_left"
    if kind in {"chart", "timeline", "document", "evidence", "map", "diagram"}:
        return "parallax"
    if kind in {"still", "image", "photo"}:
        return "slow_zoom_in"
    return "static"


def build_edit_decision_list(
    shot_timeline: list[dict],
    sections: list[dict],
    total_seconds: float,
    tone: str = "investigative",
    target_platform: str = "YouTube 16:9",
    requested_runtime_minutes: float | None = None,
) -> dict:
    """Build a deterministic EDL and QA report from existing narration/asset timings."""
    profile, min_shot, max_shot = _pacing_profile(tone)
    timeline = []
    missing = []
    checks, fixes = [], []
    chapter_grades = {}
    music = []
    previous_end = 0.0
    chapter_lookup = {}
    for chapter_index, section in enumerate(sections, start=1):
        chapter_lookup[chapter_index - 1] = section
        chapter_grades[str(chapter_index)] = (
            "cool charcoal investigative grade; preserve natural skin tones"
            if profile == "medium" else
            "neutral editorial grade; preserve natural skin tones"
        )
        start = max(0.0, float(section.get("_start", 0.0) or 0.0))
        end = min(float(total_seconds), start + max(0.0, float(section.get("_dur", 0.0) or 0.0)))
        if end > start:
            music.append({
                "chapter": chapter_index, "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "mood": "restrained investigative pulse; soften for human consequences",
                "bpm": 78 if profile == "medium" else (104 if profile == "fast" else 62),
                "instrumentation": "low strings, muted pulse, restrained piano and soft atmospheric texture",
                "search_or_generate_prompt": (
                    "Instrumental documentary underscore, no vocals, restrained investigative tension, "
                    "subtle recurring three-note motif, no trailer clichés, clean mix with space for narration"
                ),
                "volume_db": -24.0, "fade_in": 1.0, "fade_out": 2.0,
                "duck_under_voice_db": -10,
            })

    ordered = sorted(
        enumerate(shot_timeline, start=1),
        key=lambda pair: (float(pair[1].get("_start", 0.0) or 0.0), pair[0]),
    )
    durations = []
    sentence_anchors = []
    for scene_id, shot in ordered:
        start = max(0.0, float(shot.get("_start", 0.0) or 0.0))
        duration = max(0.0, float(shot.get("_dur", 0.0) or 0.0))
        end = min(float(total_seconds), start + duration)
        if end <= start:
            continue
        chapter_idx = int(shot.get("_chapter_index", 0) or 0)
        section = chapter_lookup.get(chapter_idx, {})
        narration = str(shot.get("narration") or shot.get("narration_anchor") or "")
        anchor = str(shot.get("narration_anchor") or shot.get("narration") or "").strip()
        if not anchor:
            anchor = narration.strip()
        asset = _asset_id(shot, scene_id)
        media = shot.get("_media") or {}
        if not media.get("path") or not Path(str(media.get("path"))).is_file():
            missing.append({
                "scene_id": scene_id,
                "generation_prompt": str(shot.get("visual_prompt") or
                                         "Create a factually cautious visual that illustrates: " + anchor)[:900],
            })
        overlay_text = str(shot.get("on_screen_text") or "").strip()
        overlay = []
        if overlay_text and _word_count(overlay_text) <= 8:
            overlay.append({
                "text": overlay_text, "style": "editorial_lower_third",
                "in": round(start, 3), "out": round(min(end, start + 4.0), 3),
            })
        elif overlay_text:
            fixes.append(f"Scene {scene_id}: omitted overlay longer than eight words.")
        kind = str(shot.get("visual_type") or "").lower()
        cut_reason = "emphasis" if overlay else ("new_idea" if anchor else "beat")
        transition = "cut"
        if chapter_idx != int(ordered[scene_id - 2][1].get("_chapter_index", chapter_idx) or chapter_idx) if scene_id > 1 and scene_id - 2 < len(ordered) else False:
            transition = "dip_black"
        if start < 15.0:
            cut_reason = "beat" if scene_id > 1 else "emphasis"
        timeline.append({
            "scene_id": scene_id, "start_sec": round(start, 3), "end_sec": round(end, 3),
            "visual": {
                "asset_id": asset, "motion": _visual_motion(shot),
                "crop": "16:9 safe crop; keep focal subject inside 5% title-safe margins",
                "speed": 1.0,
            },
            "cut_reason": cut_reason, "overlay": overlay,
            "transition_in": transition, "sfx": [],
            "sync_note": (
                f"Anchor: {anchor[:220]}. Current source timing is chapter/beat-level; "
                "word-accurate trigger requires supplied word timestamps."
            ),
        })
        durations.append(end - start)
        sentence_anchors.append((chapter_idx, anchor.casefold()))

    # Sentence coverage is only asserted when a matching anchor is present.
    unmatched_sentences = []
    for chapter_idx, section in chapter_lookup.items():
        sentences = _sentences(section.get("narration", ""))
        anchors = [anchor for chapter, anchor in sentence_anchors if chapter == chapter_idx and anchor]
        for sentence in sentences:
            normalized = sentence.casefold()
            if not any(normalized in anchor or anchor in normalized for anchor in anchors):
                unmatched_sentences.append({"chapter": chapter_idx + 1, "sentence": sentence[:240]})
    if unmatched_sentences:
        for item in unmatched_sentences:
            missing.append({
                "scene_id": 0,
                "generation_prompt": (
                    "Create a relevant, non-misleading documentary visual for this narration sentence: "
                    + item["sentence"]
                ),
            })
        fixes.append(f"Flagged {len(unmatched_sentences)} narration sentence(s) without a direct visual anchor.")

    repeated_lengths = 0
    for i in range(2, len(durations)):
        if (abs(durations[i] - durations[i - 1]) <= 1.0 and
                abs(durations[i - 1] - durations[i - 2]) <= 1.0):
            repeated_lengths += 1
    overlong = [
        row["scene_id"] for row in timeline
        if row["end_sec"] - row["start_sec"] > max_shot and
        row["visual"]["motion"] == "static"
    ]
    runtime_delta = None
    runtime_ok = True
    if requested_runtime_minutes is not None and requested_runtime_minutes > 0:
        target = requested_runtime_minutes * 60.0
        runtime_delta = abs(float(total_seconds) - target) / target
        runtime_ok = runtime_delta <= 0.02

    checks.extend([
        "Narration remains the master timeline; visuals are mapped to supplied beat timing.",
        "All listed assets have an explicit asset identifier and timeline interval.",
        "Music cues are instrumental recommendations with narration ducking specified.",
        "Transitions default to hard cuts; chapter boundaries are marked for editorial separation.",
        "Overlay text longer than eight words is excluded.",
    ])
    if not missing:
        checks.append("Every supplied narration sentence has a matching visual anchor.")
    if not overlong:
        checks.append("No static shot exceeds the selected pacing maximum.")
    if repeated_lengths == 0:
        checks.append("No three consecutive shots share approximately the same duration.")
    if runtime_ok:
        checks.append("Runtime is within the requested ±2% window or no separate runtime target was supplied.")
    if overlong:
        fixes.append(f"Flagged static shots exceeding {max_shot:.1f}s: {overlong}.")
    if repeated_lengths:
        fixes.append(f"Flagged {repeated_lengths} repeated shot-length rhythm pattern(s).")
    if not runtime_ok:
        fixes.append("Runtime target failed; adjust narration or edit durations before export.")

    # Explicitly disclose checks that cannot be honestly certified from current inputs.
    limitations = [
        "Word-level visual triggers are not certified without word-level narration timestamps.",
        "Visual semantic truth, source authenticity, licensing and contradiction checks require human review.",
        "Music and SFX cues are EDL instructions; this planner does not independently create or license audio assets.",
        "The current renderer still mixes one continuous music bed; per-chapter cue automation requires a separate audio-renderer upgrade.",
    ]
    return {
        "pacing_profile": profile,
        "global": {
            "fps": 30, "resolution": "1920x1080", "lufs_target": -14,
            "grade_per_chapter": chapter_grades,
            "target_platform": target_platform,
        },
        "music": music, "timeline": timeline,
        "missing_asset_requests": missing,
        "qa_report": {
            "checks_passed": checks, "fixes_applied": fixes,
            "unmatched_narration_sentences": unmatched_sentences,
            "static_shots_over_pacing_limit": overlong,
            "repeated_shot_length_patterns": repeated_lengths,
            "runtime_seconds": round(float(total_seconds), 3),
            "requested_runtime_delta_ratio": runtime_delta,
            "runtime_within_two_percent": runtime_ok,
            "limitations": limitations,
            "human_editorial_review_required": True,
        },
    }
