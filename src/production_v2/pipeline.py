"""End-to-end orchestration for the three independent v2 content pipelines."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import qa
from .artifacts import ArtifactStore, sha256_file
from .audio import analyze_wav, concat_segments, loudness_normalize
from .checkpoints import CheckpointStore
from .config import ALLOWED_CONTENT_TYPES, V2Config
from .editor import Editor
from .errors import (QualityRejected, RenderError, ResearchRejected, ValidationError,
                     V2Error)
from .licenses import AssetLibrary
from .logging import JsonLogger
from .models import Claim, Disagreement, ResearchDossier, Source, to_dict
from .narration import Narrator, select_narrator
from .notify import send_failure_notification
from .publishing import PrivatePublisher
from .research import validate_dossier
from .script_engine import build_script, validate_script
from .subtitles import build_ass, build_subtitles
from .topics import TopicHistory
from .visuals import plan_visuals


def dossier_from_dict(data: dict) -> ResearchDossier:
    return ResearchDossier(
        topic=str(data.get("topic", "")), content_type=str(data.get("content_type", "")),
        thesis=str(data.get("thesis", "")),
        sources=[Source(**{k: s.get(k, "") for k in
                          ("id", "title", "publisher", "url", "published_at", "reliability", "notes")})
                 for s in data.get("sources", [])],
        claims=[Claim(**{k: c.get(k, []) if k == "source_ids" else c.get(k, "")
                         for k in ("id", "text", "status", "source_ids", "evidence", "demonstration_only")})
                for c in data.get("claims", [])],
        disagreements=[Disagreement(**{k: d.get(k, []) if k == "source_ids" else d.get(k, "")
                                       for k in ("claim_id", "description", "source_ids", "resolution")})
                       for d in data.get("disagreements", [])],
        rejected_reason=str(data.get("rejected_reason", "")),
        demo_mode=bool(data.get("demo_mode", True)),
    )


def _run_id(content_type: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:32] or "topic"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{content_type}-{slug}-{stamp}"


def run_pipeline(content_type: str, title: str, dossier: ResearchDossier, config: V2Config, *,
                 narrator: Narrator | None = None, uploader=None,
                 run_id: str | None = None) -> dict:
    """Run one content pipeline with fail-closed QA and private-only publishing."""
    config.assert_safe()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(f"unknown content type {content_type!r}")
    rules = config.rules_for(content_type)
    run_id = run_id or _run_id(content_type, title)
    store = ArtifactStore(config.output_dir, run_id)
    logger = JsonLogger(store.path("events.jsonl"), run_id=run_id)
    checkpoints = CheckpointStore(store.path("checkpoints.json"))
    result: dict = {"run_id": run_id, "content_type": content_type, "title": title,
                    "state": "FAILED", "video_ready": False, "artifact_dir": str(store.root),
                    "demo_mode": dossier.demo_mode}
    try:
        from .deps import require_dependencies
        deps = require_dependencies(config)
        store.write_json("dependency_report.json", deps.as_dict())
        logger.info("dependencies", status=deps.status)

        if dossier.rejected_reason or dossier.content_type != content_type:
            raise ResearchRejected(dossier.rejected_reason or "dossier content type mismatch")
        research = validate_dossier(dossier, min_sources=rules.min_sources)
        store.write_json("research_assessment.json", research.as_dict())
        if not research.accepted:
            raise ResearchRejected("research dossier failed validation: "
                                   + "; ".join(research.issues[:8]))
        checkpoints.done("research")

        history = TopicHistory(config.output_dir / "topic_history.json")
        if history.is_duplicate(title, content_type):
            raise ValidationError("topic is a near-duplicate of a previous production")

        script = build_script(dossier, config, title=title)
        script_assessment = validate_script(script, dossier, rules)
        store.write_json("script.json", to_dict(script))
        store.write_json("script_assessment.json", script_assessment.as_dict())
        if not script_assessment.accepted:
            raise ValidationError("script failed validation: " + "; ".join(script_assessment.issues[:8]))
        checkpoints.done("script")

        backend = narrator or select_narrator(config)[0]
        segments = backend.synthesize(script, store.path("audio"), config)
        store.write_json("narration_segments.json", to_dict(segments))
        planned_duration = sum(s.visual.duration for s in script.scenes)
        mixed = store.path("narration_mix.wav")
        concat_segments(segments, mixed, min_duration=planned_duration)
        master = store.path("narration_master.wav")
        normalization = loudness_normalize(mixed, master, config)
        store.write_json("audio_processing.json", normalization)
        checkpoints.done("narration")

        srt_text = build_subtitles(segments, config)
        srt_path = store.write_text("subs.srt", srt_text)
        store.write_text("subs.ass", build_ass(segments, config))
        subtitle_check = qa.subtitles(srt_text, segments, config, planned_duration)
        if subtitle_check["status"] != "PASS":
            raise ValidationError("subtitle validation failed: " + "; ".join(subtitle_check["issues"][:6]))

        library = AssetLibrary()
        visuals = plan_visuals(script.scenes, store.root, library, config, content_type)
        store.write_json("asset_manifest.json", {"assets": [a.record.as_dict() for a in visuals]})
        library.write_licenses_csv(store.path("licenses.csv"))
        library.write_credits(store.path("credits.txt"))
        store.write_json("visual_plan.json", to_dict([{
            "scene_id": v.scene_id, "kind": v.kind, "description": v.description,
            "rights": v.record.license, "sha256": v.record.sha256} for v in visuals]))
        checkpoints.done("visuals")

        rendered = Editor().render(scenes=script.scenes, assets=visuals, segments=segments,
                                   mixed_audio=master, subtitles=srt_path, store=store,
                                   config=config, content_type=content_type)
        video = Path(rendered["video"])
        result["video_ready"] = video.is_file()
        checkpoints.done("render")
        logger.info("render", video=str(video), warnings=rendered.get("warnings", []))

        media = qa.technical(video, config, content_type, expected_duration=planned_duration,
                             subtitles=srt_path)
        duration = float((media.get("evidence", {}).get("probe", {}).get("format", {}) or {})
                         .get("duration") or planned_duration)
        parts = {
            "technical": media,
            "visual": qa.visual(video, store.root, config, content_type,
                                scene_count=len(script.scenes)),
            "audio": qa.audio(master, config),
            "subtitles": subtitle_check,
            "research": qa.research(dossier, research),
            "content": qa.content(script, dossier, script_assessment, history),
            "copyright": qa.copyright(library),
        }
        report = qa.build_report(parts, store, content_type=content_type, run_id=run_id)
        checkpoints.done("qa")
        logger.info("qa", decision=report.decision())
        history.record(title, content_type, "PLANNED")

        publisher = PrivatePublisher(config.output_dir / "publish_ledger.json", config)
        upload = publisher.publish(video=video, metadata={
            "title": script.title, "description": script.central_question + "\n\n" + script.takeaway,
            "tags": ["engineering", "physics", content_type]},
            quality=report, version="v1", uploader=uploader,
            content_hash=sha256_file(video))
        checkpoints.done("publish")
        store.write_json("upload_result.json", upload.__dict__)
        result["state"] = upload.state
        result["upload"] = upload.__dict__
        result["quality"] = report.decision()
        result["quality_issues"] = report.issues
        result["warnings"] = report.warnings + rendered.get("warnings", [])
    except V2Error as exc:
        result["state"] = ("QUALITY_REJECTED" if isinstance(exc, (QualityRejected, ResearchRejected))
                           else "FAILED")
        result["error"] = exc.message
        result["error_code"] = exc.code
        store.write_json("failure.json", exc.as_dict())
        logger.error("pipeline_failed", code=exc.code, error=exc.message)
        send_failure_notification(result["state"], result, config)
    except Exception as exc:  # noqa: BLE001 - unexpected failures must be visible, never green
        result["state"] = "FAILED"
        result["error"] = str(exc)[:1000]
        result["error_code"] = "UNEXPECTED_ERROR"
        store.write_json("failure.json", {"code": result["error_code"], "error": result["error"]})
        logger.error("pipeline_failed_unexpected", error=result["error"])
        send_failure_notification(result["state"], result, config)

    store.write_json("run_report.json", result)
    store.finalize_manifest()
    return result
