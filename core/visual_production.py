"""Universal documentary visual-production QA.

Creates a per-shot manifest and sampled-frame contact sheets. Metadata scores and
frame extraction are evidence for editorial review, not proof of semantic match,
historical authenticity, licensing, or lip-sync.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _source_metadata(media_path: Path) -> dict:
    sidecar = media_path.with_suffix(media_path.suffix + ".source.json")
    if not sidecar.is_file():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sample_frames(media_path: Path, out_dir: Path, shot_id: str, count: int = 3) -> list[str]:
    """Extract evenly spaced stills from a video using ffmpeg; images are copied as samples."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)
        ], stderr=subprocess.DEVNULL, text=True).strip()
        duration = max(float(raw), 0.1)
    except Exception:
        return []
    samples = []
    for index in range(count):
        timestamp = duration * (index + 1) / (count + 1)
        target = out_dir / f"{shot_id}_frame_{index + 1}.jpg"
        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(media_path),
                "-frames:v", "1", "-q:v", "3", str(target)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=45)
            if target.is_file() and target.stat().st_size > 1000:
                samples.append(str(target))
        except Exception:
            continue
    return samples


def _source_catalog(research_sources: list | None) -> dict:
    catalog = {}
    for index, source in enumerate(research_sources or []):
        if not isinstance(source, dict):
            continue
        keys = [source.get(key) for key in ("id", "source_id", "url", "title")]
        for key in keys:
            if key:
                catalog[str(key).strip()] = source
        catalog.setdefault(f"S{index + 1}", source)
    return catalog


def _audit_source_refs(shot_timeline: list[dict], research_sources: list | None) -> dict:
    """Check whether shot citations resolve to supplied research records."""
    catalog = _source_catalog(research_sources)
    linked, missing, unresolved = [], [], []
    for index, shot in enumerate(shot_timeline, start=1):
        refs = shot.get("source_refs") or []
        if isinstance(refs, str):
            refs = [refs]
        refs = [str(ref).strip() for ref in refs if str(ref).strip()]
        if not refs:
            if shot.get("chart_spec") or shot.get("timeline_events") or shot.get("on_screen_text"):
                missing.append(f"shot_{index:04d}")
            continue
        unknown = [ref for ref in refs if ref not in catalog]
        if unknown:
            unresolved.append({"shot_id": f"shot_{index:04d}", "refs": unknown})
        else:
            linked.append(f"shot_{index:04d}")
    return {
        "research_source_count": len(catalog),
        "shots_with_resolved_sources": linked,
        "evidence_graphics_missing_source_refs": missing,
        "unresolved_source_refs": unresolved,
        "citation_coverage_complete": not missing and not unresolved,
        "human_fact_check_required": True,
    }


def build_visual_review(shot_timeline: list[dict], build_dir: str | Path,
                        sample_video_frames: bool = True,
                        research_sources: list | None = None,
                        semantic_verification: bool | None = None) -> dict:
    """Record shot-level prompts, media provenance, duration and review requirements."""
    root = Path(build_dir)
    frame_dir = root / "visual_review_frames"
    shots, issues = [], []
    previous_media = None
    for index, shot in enumerate(shot_timeline):
        media = shot.get("_media") or {}
        raw_path = media.get("path")
        path = Path(raw_path) if raw_path else None
        source = _source_metadata(path) if path and path.is_file() else {}
        media_type = media.get("type", "unknown")
        shot_id = f"shot_{index + 1:04d}"
        prompt = str(shot.get("visual_prompt") or shot.get("image") or
                     shot.get("title") or shot.get("narration") or "")
        frames = []
        if path and path.is_file() and media_type == "video" and sample_video_frames:
            frames = _sample_frames(path, frame_dir, shot_id)
        elif path and path.is_file() and media_type == "image":
            frames = [str(path)]
        if not path or not path.is_file():
            issues.append({"level": "error", "shot": shot_id, "message": "Media file is missing."})
        if path and str(path) == previous_media:
            issues.append({"level": "warning", "shot": shot_id, "message": "The same media file is reused in consecutive shots."})
        previous_media = str(path) if path else None
        if media_type == "video" and not frames:
            issues.append({"level": "warning", "shot": shot_id, "message": "Could not extract sample frames; visual inspection is incomplete."})
        if source.get("relevance_score") is not None and float(source["relevance_score"]) < 0.12:
            issues.append({"level": "error", "shot": shot_id, "message": "Weak metadata relevance; replace this asset."})
        if not source:
            issues.append({"level": "warning", "shot": shot_id, "message": "No source metadata sidecar; provenance and match cannot be assessed automatically."})
        archival = str(shot.get("visual_type") or "").lower() == "archival" or bool(shot.get("archival_required"))
        archival_fields = {"item_page_present": bool(source.get("page_url") or source.get("url")), "rights_statement_present": bool(source.get("licence") or source.get("license") or source.get("rights_statement") or source.get("rights_advisory")), "creator_or_institution_present": bool(source.get("creator") or source.get("author") or source.get("provider")), "date_or_period_present": bool(source.get("date_created") or source.get("date") or source.get("time_period") or shot.get("time_period"))}
        archival_complete = all(archival_fields.values())
        if archival and not archival_complete:
            missing = [key for key, present in archival_fields.items() if not present]
            issues.append({"level": "warning", "shot": shot_id, "message": "Archival provenance needs human completion: " + ", ".join(missing)})
        shots.append({
            "shot_id": shot_id, "chapter_id": str(shot.get("chapter_id") or shot.get("chapter") or shot.get("_chapter_index") or "unassigned"),
            "start_seconds": shot.get("_start"),
            "duration_seconds": shot.get("_dur"), "media_type": media_type,
            "visual_type": shot.get("visual_type", "unknown"),
            "archival_material": archival,
            "archival_provenance_fields": archival_fields if archival else None,
            "archival_provenance_complete": archival_complete if archival else None,
            "archival_authenticity_status": "pending_human_verification" if archival else "not_applicable",
            "presenter_mode": shot.get("presenter_mode"),
            "presenter_sync_status": shot.get("presenter_sync_status"),
            "presenter_lip_sync_verified": shot.get("presenter_lip_sync_verified", False),
            "presenter_validation": source.get("validation") or {},
            "subject": shot.get("subject", ""), "location": shot.get("location", ""),
            "time_period": shot.get("time_period", ""),
            "shot_size": shot.get("shot_size", ""),
            "camera_movement": shot.get("camera_movement", ""),
            "visual_guardrail": shot.get("visual_guardrail", ""),
            "cinema_settings": shot.get("cinema_settings") or {},
            "cinematic_prompt": shot.get("cinematic_prompt") or "",
            "generation_status": shot.get("generation_status", "not_applicable"),
            "source_refs": shot.get("source_refs") or [],
            "chart_spec": shot.get("chart_spec"),
            "timeline_events": shot.get("timeline_events") or [],
            "on_screen_text": shot.get("on_screen_text") or "",
            "sound_design": shot.get("sound_design") or "",
            "media_path": str(path) if path else None,
            "wan_scene_id": shot.get("wan_scene_id"),
            "wan_generation_prompt": shot.get("wan_generation_prompt"),
            "ai_generated": bool(shot.get("ai_generated", False)),
            "visual_prompt": prompt[:700],
            "narration": str(shot.get("narration") or "")[:700],
            "source": source, "sample_frames": frames,
            "semantic_match_verified": False,
            "semantic_screening": {"status": "not_run", "human_review_required": True},
            "historical_authenticity_verified": False,
            "human_visual_review_required": True,
        })

    import os
    if semantic_verification is None:
        semantic_verification = os.getenv("VISUAL_SEMANTIC_CHECK", "0").strip().lower() in {"1", "true", "yes"}
    if semantic_verification:
        from core.visual_semantics import verify_shot_semantics
        try:
            max_shots = max(1, int(os.getenv("VISUAL_SEMANTIC_MAX_SHOTS", "40")))
        except ValueError:
            max_shots = 40
        for index, shot in enumerate(shots):
            if index >= max_shots:
                shot["semantic_screening"] = {
                    "status": "skipped", "reason": f"Configured per-build limit is {max_shots} shots.",
                    "human_review_required": True,
                }
            else:
                shot["semantic_screening"] = verify_shot_semantics(shot)
            status = shot["semantic_screening"].get("status", "uncertain")
            if status in {"mismatch", "uncertain", "error", "unavailable", "skipped"}:
                issues.append({
                    "level": "warning" if status in {"unavailable", "skipped"} else "error",
                    "shot": shot["shot_id"],
                    "message": f"AI visual-to-narration screening status is '{status}'; inspect this shot manually.",
                })
    report = {
        "shot_count": len(shots), "shots": shots, "issues": issues,
        "research_audit": _audit_source_refs(shot_timeline, research_sources),
        "automated_vision_verification": bool(semantic_verification),
        "semantic_screening_enabled": bool(semantic_verification),
        "semantic_screening_summary": {
            status: sum(1 for shot in shots
                        if shot.get("semantic_screening", {}).get("status") == status)
            for status in ("match", "mismatch", "uncertain", "unavailable", "error", "skipped", "not_run")
        },
        "shot_type_counts": {kind: sum(1 for shot in shots if shot.get("visual_type") == kind)
                             for kind in sorted({shot.get("visual_type", "unknown") for shot in shots})},
        "historical_authenticity_verified": False,
        "archival_shot_count": sum(1 for shot in shots if shot.get("archival_material")),
        "archival_provenance_complete_count": sum(1 for shot in shots if shot.get("archival_provenance_complete") is True),
        "archival_human_verification_required": True,
        "publication_ready": False,
        "notice": "AI semantic screening is a fallible triage signal, not fact-checking or proof of historical authenticity, identity, rights, licensing or lip-sync. A human must confirm every visual, inspect provenance, fact-check claims and approve disclosures.",
    }
    output = root / "visual_review_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Every new manifest starts with explicit pending decisions; nothing is pre-approved.
    from core.editorial_review import load_reviews
    load_reviews(output, shots)
    return report
