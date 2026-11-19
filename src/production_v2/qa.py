"""Multi-stage quality control; a successful FFmpeg command is not enough."""
from __future__ import annotations

import json
from pathlib import Path

from .artifacts import validate_file
from .audio import analyze_wav
from .config import V2Config
from .ffmpeg_io import average_hash, black_intervals, extract_frames, hamming, probe
from .licenses import AssetLibrary
from .models import (NarrationSegment, QualityReport, ResearchDossier, Script,
                     to_dict)
from .research import ResearchAssessment
from .script_engine import ScriptAssessment
from .subtitles import validate_subtitles
from .topics import TopicHistory


def _status(issues: list[str], warnings: list[str] | None = None) -> str:
    return "FAIL" if issues else "PASS"


def technical(video: Path, config: V2Config, content_type: str, *,
              expected_duration: float, subtitles: Path) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    evidence: dict = {}
    try:
        evidence["file"] = validate_file(video, min_bytes=50_000, label="final MP4")
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAIL", "issues": [str(exc)], "warnings": [], "evidence": {}}
    rules = config.rules_for(content_type)
    try:
        media = probe(video, config)
    except Exception as exc:  # noqa: BLE001
        return {"status": "FAIL", "issues": [f"ffprobe failed: {exc}"], "warnings": [], "evidence": evidence}
    evidence["probe"] = media
    streams = media.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        issues.append("no video stream")
    if not any(s.get("codec_type") == "audio" for s in streams):
        issues.append("no audio stream")
    for stream in streams:
        if stream.get("codec_type") == "video":
            if (stream.get("width"), stream.get("height")) != (rules.width, rules.height):
                issues.append(f"resolution {stream.get('width')}x{stream.get('height')} "
                              f"does not match {rules.width}x{rules.height}")
    try:
        duration = float((media.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    evidence["duration_seconds"] = duration
    tolerance = max(5.0, expected_duration * 0.25)
    if not duration or abs(duration - expected_duration) > tolerance:
        issues.append(f"duration {duration:.1f}s differs from expected {expected_duration:.1f}s")
    intervals = black_intervals(video, config)
    evidence["black_intervals"] = intervals
    if any(end - start > 2.0 for start, end in intervals):
        issues.append("unintended black-screen section longer than two seconds")
    if not subtitles.is_file() or subtitles.stat().st_size < 20:
        issues.append("English subtitle file is missing")
    return {"status": _status(issues), "issues": issues, "warnings": warnings, "evidence": evidence}


def visual(video: Path, store: Path, config: V2Config, content_type: str,
           *, scene_count: int) -> dict:
    rules = config.rules_for(content_type)
    frames = extract_frames(video, Path(store) / "qa_frames", config)
    evidence: dict = {"sampled_frames": len(frames)}
    issues: list[str] = []
    warnings: list[str] = []
    if len(frames) < 4:
        warnings.append("too few sampled frames for a confident visual scan")
        return {"status": "PASS", "issues": issues, "warnings": warnings, "evidence": evidence}
    hashes = [average_hash(f) for f in frames]
    counts: dict[str, int] = {}
    for value in hashes:
        counts[value] = counts.get(value, 0) + 1
    repeated_ratio = max(counts.values()) / len(hashes)
    changes = sum(1 for a, b in zip(hashes, hashes[1:]) if hamming(a, b) > 8)
    blank = sum(1 for value in hashes if value.count("1") <= 2 or value.count("1") >= 62)
    evidence.update({"repeated_frame_ratio": round(repeated_ratio, 3),
                     "scene_changes_detected": changes, "blank_frames": blank})
    if repeated_ratio > rules.static_frame_limit and scene_count > 1:
        issues.append("static-card domination: sampled frames are almost all identical")
    if blank / len(hashes) > 0.3:
        issues.append("too many blank sampled frames")
    required_changes = min(3, max(1, scene_count - 1))
    if changes < required_changes:
        warnings.append(f"only {changes} visual changes detected; inspect scene continuity")
    return {"status": _status(issues), "issues": issues, "warnings": warnings, "evidence": evidence}


def audio(wav: Path, config: V2Config) -> dict:
    report = analyze_wav(wav)
    return {"status": report["status"], "issues": report.get("issues", []),
            "warnings": [], "evidence": report}


def subtitles(srt: str, segments: list[NarrationSegment], config: V2Config,
              video_duration: float | None = None) -> dict:
    assessment = validate_subtitles(srt, segments, config, video_duration)
    return {"status": "PASS" if assessment.accepted else "FAIL",
            "issues": assessment.issues, "warnings": [], "evidence": {"cues": assessment.cues}}


def research(dossier: ResearchDossier, assessment: ResearchAssessment) -> dict:
    issues = list(assessment.issues)
    warnings = list(assessment.warnings)
    if dossier.demo_mode:
        issues.append("demo/synthetic sources cannot support a publication-ready research verdict")
    return {"status": _status(issues), "issues": issues, "warnings": warnings,
            "evidence": assessment.as_dict()}


def content(script: Script, dossier: ResearchDossier, assessment: ScriptAssessment,
            history: TopicHistory) -> dict:
    issues = list(assessment.issues)
    warnings = list(assessment.warnings)
    if history.is_duplicate(script.title, script.content_type):
        issues.append("topic/story duplicate in history")
    if not script.scenes:
        issues.append("script has no scenes")
    return {"status": _status(issues), "issues": issues, "warnings": warnings,
            "evidence": {"scene_count": len(script.scenes),
                         "claim_count": len(dossier.claims),
                         "script_claim_ids": script.claim_ids}}


def copyright(library: AssetLibrary) -> dict:
    decisions = library.decisions()
    blocked = [d for d in decisions if d["status"] == "blocked"]
    incomplete = [d for d in decisions if d["status"] == "incomplete"]
    issues = ([f"blocked asset: {d['asset']}" for d in blocked]
              + [f"incomplete rights record: {d['asset']}" for d in incomplete])
    return {"status": _status(issues), "issues": issues, "warnings": [],
            "evidence": {"decisions": decisions}}


def build_report(parts: dict[str, dict], store, *, content_type: str, run_id: str) -> QualityReport:
    report = QualityReport(
        technical_quality=parts["technical"]["status"],
        visual_quality=parts["visual"]["status"],
        audio_quality=parts["audio"]["status"],
        subtitle_quality=parts["subtitles"]["status"],
        research_quality=parts["research"]["status"],
        copyright_compliance=parts["copyright"]["status"],
        issues=[f"{name}: {issue}" for name, part in parts.items() for issue in part.get("issues", [])],
        warnings=[f"{name}: {warning}" for name, part in parts.items() for warning in part.get("warnings", [])],
        evidence={"content_type": content_type, "run_id": run_id,
                  "checks": {name: part.get("status") for name, part in parts.items()},
                  "details": to_dict({name: part.get("evidence", {}) for name, part in parts.items()})},
    )
    report.finalize()
    store.write_json("quality_report.json", to_dict(report))
    store.write_json("quality_decision.json", report.decision())
    return report
