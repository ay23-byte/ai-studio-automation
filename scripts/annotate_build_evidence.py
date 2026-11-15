#!/usr/bin/env python3
"""Print the measured facts of a documentary build as notices on the workflow run page.

The review artifact holds the full evidence, but it has to be downloaded and unpacked.
These lines put the figures a reviewer needs first directly on the run page: the final
file, how long it is against the requested runtime, where each visual came from, what
each footage source answered, the presenter measurements, subtitles, audio and research.

Every figure is read from a file the build wrote or measured from the video itself;
nothing is estimated. A missing report is stated as missing. The script always exits 0:
reporting must never change the result of a production run.

    python scripts/annotate_build_evidence.py --pipeline longform --target-minutes 22
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_MESSAGE = 3000         # keeps every footage source on one notice
MAX_NOTICES = 10           # a step may show at most ten notices


def _load(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean(text) -> str:
    text = " ".join(str(text).split()).replace("::", ": :")
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE - 1] + "…"


def _minutes(seconds) -> str:
    seconds = float(seconds)
    return f"{int(seconds // 60)} min {int(round(seconds % 60)):02d} s"


def final_video(report: dict, target_minutes: float | None) -> str | None:
    output = report.get("output") or {}
    if not output.get("exists"):
        return "Final video: no finished file was produced."
    parts = [str(output.get("file"))]
    duration = output.get("duration_seconds")
    if duration:
        parts.append(_minutes(duration))
        if target_minutes:
            share = float(duration) / (target_minutes * 60)
            parts.append(f"{share:.0%} of the requested {target_minutes:g} minutes")
    if output.get("width") and output.get("height"):
        parts.append(f"{output['width']}x{output['height']}")
    codecs = "/".join(str(output[key]) for key in ("video_codec", "audio_codec") if output.get(key))
    if codecs:
        parts.append(codecs)
    if output.get("size_mb"):
        parts.append(f"{output['size_mb']} MB")
    return "Final video: " + " | ".join(parts)


def script_and_voice(report: dict) -> str | None:
    model, voice = report.get("text_model") or {}, report.get("voice") or {}
    parts = []
    if model.get("model"):
        parts.append(f"script by {model.get('provider')} / {model.get('model')}"
                     + (" (fallback model)" if model.get("fallback_used") else ""))
    if voice.get("provider"):
        text = f"narration by {voice.get('provider')} ({voice.get('voice')})"
        if voice.get("status") == "fallback":
            text += f", FALLBACK from {voice.get('requested_provider')}: {voice.get('fallback_reason')}"
        parts.append(text)
    return ("Script and narration: " + " | ".join(parts)) if parts else None


def visuals(report: dict, build: Path) -> str | None:
    section = report.get("visuals") or {}
    review = _load(build / "visual_review_report.json")
    shots = review.get("shots") or []
    if not section.get("providers") and not shots:
        return None
    parts = []
    if shots:
        kinds: dict[str, int] = {}
        for shot in shots:
            kind = ("presenter" if shot.get("presenter_mode") else
                    f"{shot.get('visual_type') or 'unknown'} ({shot.get('media_type') or '?'})")
            kinds[kind] = kinds.get(kind, 0) + 1
        parts.append(f"{len(shots)} shots: " + ", ".join(
            f"{name} x{count}" for name, count in sorted(kinds.items(), key=lambda item: -item[1])))
    if section.get("providers"):
        parts.append("sources: " + ", ".join(f"{name} x{count}" for name, count in section["providers"].items()))
    cards = section.get("fallback_card_count")
    if cards:
        share = section.get("fallback_card_share")
        parts.append(f"{cards} fallback cards" + (f" ({float(share):.0%} of visuals)" if share is not None else ""))
    else:
        parts.append("no fallback cards")
    return "Visuals: " + " | ".join(parts)


def footage_sources(build: Path) -> str | None:
    data = _load(build / "provider_report.json")
    providers = data.get("providers") or []
    if not providers:
        return None
    summary = data.get("summary") or {}
    parts = [f"{summary.get('scenes_with_footage', 0)} of {summary.get('scenes_searched', 0)} searched scenes got footage"]
    for entry in providers:
        if not entry.get("searches") and not entry.get("failure_reason") and not entry.get("disabled_reason"):
            continue
        text = (f"{entry.get('provider')}: {entry.get('searches', 0)} searches, {entry.get('results_found', 0)} results, "
                f"{entry.get('download_successes', 0)} valid downloads, {entry.get('selected_scenes', 0)} used")
        reasons = entry.get("rejection_reasons") or {}
        if reasons:
            reason, count = max(reasons.items(), key=lambda item: item[1])
            text += f", most refused for '{reason}' x{count}"
        problem = entry.get("disabled_reason") or entry.get("failure_reason")
        if problem:
            text += f", problem: {str(problem)[:140]}"
        parts.append(text)
    return "Footage sources: " + " | ".join(parts)


def narration(build: Path) -> str | None:
    runtime = _load(build / "runtime_report.json")
    voice = _load(build / "narration_synthesis_report.json")
    if not runtime and not voice:
        return None
    parts = []
    if runtime:
        parts.append(f"{runtime.get('words')} words spoken in {runtime.get('narration_minutes')} min "
                     f"({runtime.get('words_per_minute')} words a minute)")
        if runtime.get("target_minutes"):
            parts.append(f"{float(runtime.get('fraction_of_target') or 0):.0%} of the requested "
                         f"{float(runtime['target_minutes']):g} min, floor {float(runtime.get('minimum_fraction') or 0):.0%}: "
                         f"{runtime.get('status')}")
    if voice:
        parts.append(f"{voice.get('chunk_count')} phrases, {voice.get('speech_seconds')} s of speech + "
                     f"{voice.get('inserted_pause_seconds')} s of pauses")
        if voice.get("characters_per_second") is not None:
            parts.append(f"{voice['characters_per_second']} characters a second")
        if voice.get("voice"):
            text = f"voice {voice['voice']} ({voice.get('gender') or 'gender not recorded'} requested)"
            if voice.get("configured_voice") and voice["voice"] != voice["configured_voice"]:
                text += f", NOT the configured voice {voice['configured_voice']}"
            parts.append(text)
    return "Narration: " + " | ".join(parts)


def scenes_without_footage(build: Path, limit: int = 6) -> str | None:
    data = _load(build / "provider_report.json")
    missing = [scene for scene in (data.get("scenes") or []) if scene.get("status") == "needs_manual_review"]
    if not missing:
        return None
    reasons: dict[str, int] = {}
    for scene in missing:
        reason = str(scene.get("reason") or "no reason recorded")
        reasons[reason] = reasons.get(reason, 0) + 1
    parts = [f"{len(missing)} scenes got no footage",
             "reasons: " + "; ".join(f"{reason} x{count}" for reason, count in
                                     sorted(reasons.items(), key=lambda item: -item[1])[:4])]
    for scene in missing[:limit]:
        queries = ", ".join(f"'{query}'" for query in (scene.get("queries") or [])[:3])
        parts.append(f"{scene.get('scene')}: searched {queries} ({scene.get('candidates_found', 0)} found, "
                     f"{scene.get('candidates_eligible', 0)} eligible)")
    return "Scenes without footage: " + " | ".join(parts)


def presenter(build: Path) -> str | None:
    shots = [shot for shot in (_load(build / "visual_review_report.json").get("shots") or [])
             if shot.get("presenter_mode")]
    if not shots:
        return None
    measured = [shot["presenter_motion"] for shot in shots if isinstance(shot.get("presenter_motion"), dict)]
    text = f"Presenter: {len(shots)} clips ({shots[0].get('presenter_mode')})"
    if not measured:
        return text + " | motion was not measured for this presenter mode | human review pending"
    rates = [m["distinct_frame_rate"] for m in measured if m.get("distinct_frame_rate") is not None]
    freezes = [m["longest_frozen_seconds"] for m in measured if m.get("longest_frozen_seconds") is not None]
    mouths = [m["voiced_mouth_motion_ratio"] for m in measured if m.get("voiced_mouth_motion_ratio") is not None]
    offsets = [abs(m["mouth_track_offset_frames"]) for m in measured if m.get("mouth_track_offset_frames") is not None]
    passed = sum(1 for m in measured if m.get("status") == "PASS")
    parts = [text, f"{passed} of {len(measured)} passed the motion check"]
    if rates:
        parts.append(f"lowest {min(rates):.1f} different frames per second")
    if freezes:
        parts.append(f"longest freeze {max(freezes):.2f} s")
    if mouths:
        parts.append(f"mouth moving in at least {min(mouths):.0%} of voiced frames")
    if offsets:
        parts.append(f"largest mouth-to-audio offset {max(offsets)} frames")
    parts.append("human review of lip-sync pending")
    return " | ".join(parts)


def subtitles(build: Path) -> str | None:
    data = _load(build / "subtitle_translation.json")
    if not data:
        return None
    check = data.get("english_check") or {}
    parts = [f"language {data.get('target')}", f"mode {data.get('mode')}"]
    if check:
        parts.append(f"English check {check.get('status')} on {check.get('lines')} lines "
                     f"({check.get('devanagari_lines', 0)} Devanagari, {check.get('roman_hindi_lines', 0)} Hindi in Latin letters)")
    else:
        parts.append("English check not recorded")
    return "Subtitles: " + " | ".join(parts)


def audio(build: Path) -> str | None:
    data = _load(build / "audio_quality_report.json")
    metrics = data.get("metrics") or {}
    if not metrics:
        return None
    parts = [f"status {data.get('status')}"]
    for key, label, unit in (("integrated_lufs", "loudness", "LUFS"), ("true_peak_dbfs", "true peak", "dBTP"),
                             ("loudness_range_lu", "range", "LU")):
        if metrics.get(key) is not None:
            parts.append(f"{label} {float(metrics[key]):.1f} {unit}")
    if metrics.get("clipped_samples") is not None:
        parts.append(f"{metrics['clipped_samples']} clipped samples")
    parts.append(f"{metrics.get('long_silence_count', 0)} silences over 3.5 s")
    parts.append(f"listening review {data.get('human_review_status')}")
    return "Audio: " + " | ".join(parts)


def research(build: Path) -> str | None:
    data = _load(build / "research_verification_report.json")
    if not data:
        return None
    claims = data.get("claims") or []
    linked = sum(1 for item in claims if item.get("traceability_status") == "source_linked_pending_human_review")
    sources = data.get("sources") or []
    valid = sum(1 for item in sources if item.get("url_valid"))
    return (f"Research: {len(sources)} sources ({valid} with a valid URL) | {len(claims)} claims, "
            f"{linked} linked to a source | fact check by a person still required")


def storyboard(build: Path) -> str | None:
    """How many pictures each chapter was planned with, and where missing ones came from."""
    script = _load(build / "script.json")
    sections = [item for item in (script.get("sections") or []) if isinstance(item, dict)]
    if not sections:
        return None
    counts = [len(item.get("visuals") or []) for item in sections]
    kinds: dict[str, int] = {}
    for item in sections:
        for beat in item.get("visuals") or []:
            if isinstance(beat, dict):
                kind = str(beat.get("visual_type") or "unknown")
                kinds[kind] = kinds.get(kind, 0) + 1
    text = (f"storyboard {sum(counts)} beats in {len(sections)} chapters ({', '.join(map(str, counts))}); "
            + ", ".join(f"{kind} x{number}" for kind, number in sorted(kinds.items(), key=lambda pair: -pair[1])))
    done = [item for item in (script.get("storyboard_completion") or []) if isinstance(item, dict)]
    if done:
        by_source: dict[str, int] = {}
        for item in done:
            by_source[str(item.get("source"))] = by_source.get(str(item.get("source")), 0) + 1
        removed = sum(int(item.get("placeholders_removed") or 0) for item in done)
        text += ("; completion: " + ", ".join(f"{name} x{number}" for name, number in sorted(by_source.items()))
                 + f", {removed} placeholder beats removed")
    return text


def visual_checks(build: Path, limit: int = 6) -> str | None:
    """Result of the picture audit that decides technical QA, with the shots that fail it."""
    data = _load(build / "visual_consistency_report.json")
    summary = data.get("summary") or {}
    if not data or not summary:
        return None
    text = (f"picture audit {data.get('status')}: {summary.get('pass_count', 0)} pass, "
            f"{summary.get('review_count', 0)} review, {summary.get('fail_count', 0)} fail, "
            f"{summary.get('unavailable_count', 0)} not measured")
    failed = [row for row in (data.get("shots") or []) if isinstance(row, dict) and row.get("status") == "fail"]
    if failed:
        text += "; failing: " + "; ".join(
            f"{row.get('shot_id')} {' / '.join(str(item) for item in (row.get('issues') or [])[:2])}"
            for row in failed[:limit])
        if len(failed) > limit:
            text += f" (+{len(failed) - limit} more)"
    return text


def evidence_lines(report: dict, build: Path, target_minutes: float | None) -> list[str]:
    # A step shows at most ten notices, so the storyboard and the picture audit ride on the
    # script and visuals lines instead of taking a notice each.
    script_line, plan = script_and_voice(report), storyboard(build)
    if plan:
        script_line = f"{script_line} | {plan}" if script_line else f"Script and narration: {plan}"
    visuals_line, audit = visuals(report, build), visual_checks(build)
    repairs = _load(build / "picture_repair_report.json")
    if repairs.get("shots"):
        audit = ((audit + " | ") if audit else "") + (
            f"{repairs.get('replaced', 0)} unusable pictures replaced before the edit, "
            f"{repairs.get('unresolved', 0)} not replaceable")
    if audit:
        visuals_line = f"{visuals_line} | {audit}" if visuals_line else f"Visuals: {audit}"
    lines = [final_video(report, target_minutes), script_line, narration(build),
             visuals_line, footage_sources(build), scenes_without_footage(build),
             presenter(build), subtitles(build), audio(build), research(build)]
    return [_clean(line) for line in lines if line][:MAX_NOTICES]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pipeline", default="longform")
    parser.add_argument("--job-id", type=int, default=None)
    parser.add_argument("--target-minutes", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        from core import run_report
        from core.config import BUILD_DIR
        report = run_report.collect(args.pipeline, args.job_id, job_status=os.getenv("JOB_STATUS"))
        if not report.get("job_id"):
            print("Build evidence: no job was created in this run.")
            return 0
        build = Path(BUILD_DIR) / f"job_{report['job_id']}"
        for line in evidence_lines(report, build, args.target_minutes):
            title, _, message = line.partition(": ")
            print(f"::notice title=Build evidence — {title}::{message}" if os.getenv("GITHUB_ACTIONS") else line)
    except Exception:  # reporting must never fail a production run
        print("WARNING: build evidence could not be written:", file=sys.stderr)
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
