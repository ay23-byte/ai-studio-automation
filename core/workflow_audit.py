"""End-to-end documentary workflow audits.

This module adds deterministic checks around research, storyboard, media provenance,
presenter timing, and rendered exports. It does not claim to verify facts from the
internet: research claims remain unverified until a human/editor supplies evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _issue(level: str, stage: str, message: str, **details: Any) -> dict:
    item = {"level": level, "stage": stage, "message": message}
    if details:
        item["details"] = details
    return item


def audit_research(research: dict | None) -> dict:
    """Audit evidence metadata; never infer that a claim is true from a URL alone."""
    research = research or {}
    sources = research.get("sources") or []
    claims = research.get("claims") or []
    issues = []
    source_ids = set()
    for index, source in enumerate(sources):
        sid = str(source.get("id") or f"source-{index + 1}")
        source_ids.add(sid)
        if not source.get("url"):
            issues.append(_issue("warning", "research", "Source has no URL.", source_id=sid))
        if not source.get("publisher") and not source.get("title"):
            issues.append(_issue("warning", "research", "Source has no publisher or title.", source_id=sid))
        if not source.get("published_at"):
            issues.append(_issue("info", "research", "Publication date is missing; recency cannot be assessed.", source_id=sid))
    audited = []
    for index, claim in enumerate(claims):
        refs = claim.get("source_ids") or []
        status = claim.get("status", "needs_verification")
        supported = bool(refs) and any(str(ref) in source_ids for ref in refs) and bool(claim.get("evidence"))
        if status == "verified" and not supported:
            status = "needs_verification"
        if not supported:
            issues.append(_issue("warning", "research", "Claim lacks linked source evidence and remains unverified.",
                                 claim_id=claim.get("id", index + 1)))
        audited.append({
            "id": claim.get("id", index + 1),
            "text": claim.get("text", ""),
            "status": "source_supported_pending_editor_review" if supported else "needs_verification",
            "source_ids": refs,
            "evidence": claim.get("evidence", ""),
        })
    return {
        "source_count": len(sources),
        "claim_count": len(claims),
        "claims": audited,
        "fact_check_complete": False,
        "notice": "Automated metadata checks are not independent fact verification. An editor must inspect sources and evidence.",
        "issues": issues,
    }


def audit_storyboard(scenes: list[dict] | None) -> dict:
    """Check narration anchors, visual intent, ordering, and timestamp continuity."""
    scenes = scenes or []
    issues = []
    previous_end = 0.0
    audited = []
    for index, scene in enumerate(scenes):
        number = scene.get("scene_number", index + 1)
        narration = str(scene.get("narration") or scene.get("say") or "").strip()
        visual = str(scene.get("visual_prompt") or scene.get("image") or scene.get("keywords") or "").strip()
        start, end = scene.get("start_seconds"), scene.get("end_seconds")
        if not narration:
            issues.append(_issue("error", "storyboard", "Scene has no narration anchor.", scene=number))
        if not visual:
            issues.append(_issue("warning", "storyboard", "Scene has no visual prompt or footage keywords.", scene=number))
        try:
            start, end = float(start), float(end)
            if start < 0 or end <= start:
                raise ValueError
            if start + 0.05 < previous_end:
                issues.append(_issue("error", "storyboard", "Scene timestamps overlap or are out of order.", scene=number))
            previous_end = end
        except (TypeError, ValueError):
            issues.append(_issue("error", "storyboard", "Scene needs valid start_seconds and end_seconds.", scene=number))
            start = end = None
        audited.append({"scene_number": number, "has_narration": bool(narration),
                        "has_visual_intent": bool(visual), "start_seconds": start, "end_seconds": end})
    return {"scene_count": len(scenes), "scenes": audited, "issues": issues,
            "ready_for_media": not any(x["level"] == "error" for x in issues)}


def audit_media_assets(assets: list[dict] | None, required_scenes: list[int] | None = None) -> dict:
    """Check scene coverage and provenance/licence review fields for selected assets."""
    assets = assets or []
    required = set(required_scenes or [])
    covered, issues, rows = set(), [], []
    for index, asset in enumerate(assets):
        scene = asset.get("scene_number")
        if scene is not None:
            covered.add(scene)
        provider = asset.get("provider") or asset.get("source")
        page_url = asset.get("page_url") or asset.get("source_url")
        licence = asset.get("licence") or asset.get("licence_status")
        path = asset.get("path")
        relevance = asset.get("relevance_score")
        visual_type = asset.get("visual_type") or asset.get("media_type") or "unknown"
        if relevance is None:
            issues.append(_issue("warning", "visual_authenticity", "No scene-to-asset relevance score is recorded; content match is unverified.", asset=index + 1))
        elif float(relevance) < 0.12:
            issues.append(_issue("error", "visual_authenticity", "Asset metadata is a weak match for its scene and should be rejected.", asset=index + 1, relevance_score=relevance))
        if visual_type == "ai_reconstruction" and asset.get("disclosure_ready") is not True:
            issues.append(_issue("warning", "visual_authenticity", "AI reconstruction needs an on-screen or editorial disclosure.", asset=index + 1))
        if asset.get("human_visual_reviewed") is not True:
            issues.append(_issue("warning", "visual_authenticity", "A human must confirm depicted subject, location and historical period before publication.", asset=index + 1))
        if not provider or not page_url:
            issues.append(_issue("warning", "footage", "Asset provenance is incomplete.", asset=index + 1))
        if not licence or asset.get("licence_reviewed") is not True:
            issues.append(_issue("warning", "footage", "Asset licence needs human review before publication.", asset=index + 1))
        if path and not Path(path).is_file():
            issues.append(_issue("error", "footage", "Referenced media file does not exist.", asset=index + 1))
        rows.append({"scene_number": scene, "provider": provider, "path": path,
                     "relevance_score": relevance, "visual_type": visual_type,
                     "human_visual_reviewed": asset.get("human_visual_reviewed") is True,
                     "licence_reviewed": asset.get("licence_reviewed") is True})
    missing = sorted(required - covered)
    for scene in missing:
        issues.append(_issue("warning", "footage", "No real-footage asset is assigned to this scene.", scene=scene))
    return {"asset_count": len(assets), "covered_scenes": sorted(covered, key=str),
            "missing_scenes": missing, "assets": rows, "issues": issues,
            "publication_ready": not any(x["level"] in {"error", "warning"} for x in issues)}


def audit_presenter_timing(chapters: list[dict] | None, tolerance_seconds: float = 0.75) -> dict:
    """Compare prepared presenter clip durations to chapter narration durations."""
    issues, rows = [], []
    for index, chapter in enumerate(chapters or []):
        expected = chapter.get("narration_duration_seconds")
        actual = chapter.get("presenter_duration_seconds")
        if expected is None or actual is None:
            issues.append(_issue("warning", "presenter", "Presenter timing is unavailable; synchronisation is unverified.",
                                 chapter=chapter.get("chapter", index + 1)))
            continue
        try:
            delta = abs(float(expected) - float(actual))
        except (TypeError, ValueError):
            delta = float("inf")
        ok = delta <= tolerance_seconds
        rows.append({"chapter": chapter.get("chapter", index + 1),
                     "narration_duration_seconds": expected,
                     "presenter_duration_seconds": actual,
                     "difference_seconds": round(delta, 3) if delta != float("inf") else None,
                     "duration_within_tolerance": ok})
        if not ok:
            issues.append(_issue("warning", "presenter",
                                 "Presenter clip duration differs from narration; check timing or lip-sync.",
                                 chapter=chapter.get("chapter", index + 1)))
    return {"chapters_checked": len(rows), "chapters": rows, "issues": issues,
            "lip_sync_verified": False,
            "notice": "Duration agreement alone cannot prove mouth-to-audio lip-sync."}


def build_workflow_report(research: dict | None, scenes: list[dict] | None,
                          assets: list[dict] | None, chapters: list[dict] | None,
                          video_path: str | None = None, subtitle_path: str | None = None) -> dict:
    """Create a combined JSON report; final export checks use core.quality when requested."""
    report = {
        "research": audit_research(research),
        "storyboard": audit_storyboard(scenes),
        "footage": audit_media_assets(assets, [s.get("scene_number", i + 1)
                                                for i, s in enumerate(scenes or [])]),
        "presenter": audit_presenter_timing(chapters),
        "video": None,
    }
    if video_path:
        from core.quality import inspect_video
        report["video"] = inspect_video(video_path, subtitle_path=subtitle_path)
    issues = []
    for stage in ("research", "storyboard", "footage", "presenter"):
        issues.extend(report[stage]["issues"])
    if report["video"]:
        issues.extend({"level": "warning", "stage": "video", "message": message}
                      for message in report["video"].get("warnings", []))
        issues.extend({"level": "error", "stage": "video", "message": message}
                      for message in report["video"].get("issues", []))
    report["issues"] = issues
    report["summary"] = {
        "error_count": sum(x["level"] == "error" for x in issues),
        "warning_count": sum(x["level"] == "warning" for x in issues),
        "human_review_required": True,
        "publish_recommendation": "review_required" if issues else "technical_checks_passed_editor_review_required",
    }
    return report


def save_workflow_report(report: dict, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
