"""One structured, non-sensitive report per production run.

Why this exists
---------------
A failed or skipped workflow run used to leave the operator with a red cross
and a log to scroll through.  The facts needed to understand a run were already
on disk (the job row, ``quality_report.json``, ``narration_synthesis_report.json``,
``asset_manifest.json``, ``release_gate.json``, ``upload_result.json``, the
checkpoint) but nothing put them in one place.

``collect`` reads those files and returns one dictionary; ``to_markdown`` turns
it into the GitHub job summary; ``write`` stores ``run_report.json`` for the
artifact and the dashboard.  The module only *reads* production state.  It never
raises into a workflow and never includes credentials: values are taken from
report files, not from the environment, and free text is passed through the
shared redactor.

Every field is reported as found.  When evidence is missing the report says
``unknown`` or ``not recorded`` instead of assuming success.
"""
from __future__ import annotations

import csv
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

PIPELINES = ("shorts", "longform", "news_short")
PIPELINE_ALIASES = {"short": "shorts", "long": "longform", "documentary": "longform",
                    "news": "news_short", "news-short": "news_short"}

# Outcomes, most specific first.
UPLOADED = "uploaded"
UPLOAD_BLOCKED = "upload_blocked"
BUILT = "built"
SKIPPED = "skipped"
FAILED = "failed"

_HEADLINE = {
    UPLOADED: "UPLOADED (private)",
    UPLOAD_BLOCKED: "BUILT, UPLOAD BLOCKED",
    BUILT: "BUILT, NOT UPLOADED",
    SKIPPED: "SKIPPED (nothing was built)",
    FAILED: "FAILED",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact(value) -> str:
    text = str(value if value is not None else "")
    try:
        from core.llm import redact
        text = redact(text)
    except Exception:
        pass
    return text


def _load(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalise_pipeline(value) -> str:
    name = str(value or "").strip().lower()
    return PIPELINE_ALIASES.get(name, name)


# ---------------------------------------------------------------------------
# Locating the job this run produced
# ---------------------------------------------------------------------------
def _rows(limit: int = 200) -> list[dict]:
    try:
        from core import db
        return [dict(row) for row in db.list_videos(limit=limit)]
    except Exception:
        return []


def find_job(pipeline: str | None = None, job_id: int | None = None, *, build_root=None) -> dict | None:
    """Return the job row this run worked on, or ``None``.

    Without an explicit id, only jobs that have a build directory are considered.
    On a fresh CI runner the build directory exists only for jobs created by the
    current run, so an old row from the tracked database is never mistaken for
    this run's result.
    """
    from core import config as cfg
    build_root = Path(build_root) if build_root else cfg.BUILD_DIR
    rows = _rows()
    if job_id is not None:
        for row in rows:
            if int(row.get("id") or -1) == int(job_id):
                return row
        try:
            from core import db
            found = db.get_video(int(job_id))
            return dict(found) if found else None
        except Exception:
            return None
    wanted = normalise_pipeline(pipeline) if pipeline else None
    try:
        from core.db import normalize_format
    except Exception:
        def normalize_format(value):
            return str(value or "").lower()
    candidates = []
    for row in rows:
        directory = build_root / f"job_{row.get('id')}"
        if not directory.is_dir():
            continue
        fmt = normalize_format(row.get("format"))
        kind = str(row.get("pipeline") or fmt)
        if wanted and wanted not in {fmt, kind}:
            continue
        candidates.append(row)
    if not candidates:
        return None
    return max(candidates, key=lambda row: int(row.get("id") or 0))


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _output_section(row: dict, quality: dict) -> dict:
    path = Path(str(row.get("video_path") or "")) if row.get("video_path") else None
    exists = bool(path and path.is_file())
    metrics = quality.get("metrics") if isinstance(quality.get("metrics"), dict) else {}
    section = {
        "file": path.name if path else None,
        "exists": exists,
        "size_mb": round(path.stat().st_size / 1_048_576, 2) if exists else None,
        "duration_seconds": metrics.get("duration_seconds"),
        "width": metrics.get("width"),
        "height": metrics.get("height"),
        "video_codec": metrics.get("video_codec"),
        "audio_codec": metrics.get("audio_codec"),
        "measured_by": "quality_report.json" if metrics else None,
    }
    if exists and not metrics:
        # No QA report: measure the file directly rather than leave it blank.
        try:
            from core.ffmpeg_utils import probe_streams
            probed = probe_streams(path)
            section.update({
                "duration_seconds": probed.get("duration_seconds"),
                "width": probed.get("width"), "height": probed.get("height"),
                "video_codec": probed.get("video_codec"), "audio_codec": probed.get("audio_codec"),
                "measured_by": "ffprobe",
            })
        except Exception:
            section["measured_by"] = None
    return section


def _voice_section(build_dir: Path) -> dict:
    report = _load(build_dir / "narration_synthesis_report.json")
    if not report:
        return {"status": "not recorded"}
    requested, actual = report.get("requested_engine"), report.get("actual_engine")
    return {
        "status": "fallback" if (report.get("fallback_reason") or (requested and requested != actual)) else "ok",
        "requested_provider": requested,
        "provider": actual,
        "voice": report.get("voice"),
        "language": report.get("language"),
        "fallback_reason": _redact(report.get("fallback_reason"))[:400] if report.get("fallback_reason") else None,
        "speech_seconds": report.get("speech_seconds"),
    }


def _visual_section(build_dir: Path, quality: dict) -> dict:
    manifest = _load(build_dir / "asset_manifest.json")
    fallback = _load(build_dir / "visual_fallback_report.json") or (
        quality.get("visual_fallback") if isinstance(quality.get("visual_fallback"), dict) else {})
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
    providers = Counter()
    for asset in assets:
        if isinstance(asset, dict):
            providers[str(asset.get("provider") or asset.get("acquired_via") or "unknown")] += 1
    if not manifest and not fallback:
        return {"status": "not recorded"}
    count = manifest.get("asset_count", len(assets)) or fallback.get("visual_count")
    return {
        "status": "ok" if not fallback.get("fallback_card_count") else
                  ("fallback_majority" if fallback.get("status") == "FAIL" else "partial_fallback"),
        "asset_count": count,
        "providers": dict(providers.most_common()),
        "fallback_card_count": fallback.get("fallback_card_count", manifest.get("fallback_card_count", 0)),
        "fallback_card_share": fallback.get("fallback_card_share"),
        "ai_generated_count": manifest.get("ai_generated_count"),
    }


def _rights_section(build_dir: Path, fmt: str) -> dict:
    manifest = _load(build_dir / "asset_manifest.json")
    licences = build_dir / "licenses.csv"
    rows = 0
    if licences.is_file():
        try:
            with licences.open(encoding="utf-8-sig", newline="") as handle:
                rows = sum(1 for _ in csv.DictReader(handle))
        except (OSError, csv.Error):
            rows = 0
    certificate = _load(build_dir / "release_certificate.json")
    missing = manifest.get("without_provenance")
    if not manifest and not licences.is_file() and not certificate:
        return {"status": "not recorded"}
    status = "recorded"
    if isinstance(missing, list) and missing:
        status = "incomplete"
    if fmt == "longform":
        status = {"CERTIFIED": "certified", "BLOCKED": "blocked"}.get(
            str(certificate.get("status") or "").upper(), "not certified")
    return {
        "status": status,
        "assets_without_provenance": len(missing) if isinstance(missing, list) else None,
        "licence_rows": rows if licences.is_file() else None,
        "credits_file": (build_dir / "credits.txt").is_file(),
        "release_certificate": str(certificate.get("status")) if certificate else None,
        "certificate_blockers": [_redact(item)[:200] for item in (certificate.get("blockers") or [])][:5],
    }


def _research_section(build_dir: Path, fmt: str) -> dict:
    if fmt == "news_short":
        verification = _load(build_dir / "verification.json")
        if not verification:
            return {"status": "not recorded"}
        return {"status": str(verification.get("status") or "unknown"),
                "source_count": len(verification.get("sources") or []) or None}
    if fmt == "longform":
        report = _load(build_dir / "research_verification_report.json")
        if not report:
            return {"status": "not recorded"}
        return {"status": "recorded, human claim review required",
                "source_count": len(report.get("sources") or []),
                "claim_count": len(report.get("claims") or [])}
    return {"status": "not applicable"}


def _qa_section(row: dict, quality: dict, gate: dict) -> dict:
    return {
        "status": str(quality.get("status") or row.get("quality_status") or "not recorded"),
        "score": quality.get("score", row.get("quality_score")),
        "issues": [_redact(item)[:240] for item in (quality.get("issues") or [])][:6],
        "warnings": [_redact(item)[:240] for item in (quality.get("warnings") or [])][:6],
        "release_gate": str(gate.get("status") or "not evaluated"),
        "release_gate_blocking": [_redact(item)[:300] for item in (gate.get("blocking") or [])][:6],
    }


def _upload_section(row: dict, build_dir: Path, gate: dict, quality: dict,
                    external_result=None, external_failure=None) -> dict:
    result = _load(build_dir / "upload_result.json")
    if not result and external_result:
        # Result file of the documentary artifact uploader.  It records the privacy
        # that was requested; it does not read the stored status back from YouTube.
        legacy = _load(external_result)
        if legacy.get("video_id"):
            result = {"youtube_id": legacy["video_id"], "url": legacy.get("url"),
                      "privacy": legacy.get("privacy_status"), "privacy_verified": None,
                      "warnings": ([] if legacy.get("processing_status") in (None, "ok") else
                                   [f"YouTube processing status: {legacy.get('processing_status')}"])}
    failure = _load(external_failure) if external_failure else {}
    youtube_id = result.get("youtube_id") or row.get("youtube_id")
    if youtube_id:
        verified = result.get("privacy_verified")
        return {
            "status": UPLOADED,
            "youtube_id": youtube_id,
            "url": result.get("url") or f"https://youtu.be/{youtube_id}",
            "privacy": result.get("privacy") or row.get("privacy_status"),
            # True only when YouTube's stored status was read back after upload.
            "privacy_verified": bool(verified) if verified is not None else None,
            "warnings": [_redact(item)[:200] for item in (result.get("warnings") or [])][:5],
        }
    if gate.get("status") == "BLOCKED":
        return {"status": "blocked", "reason": "; ".join(
            _redact(item)[:300] for item in (gate.get("blocking") or [])[:4])}
    if failure.get("error"):
        return {"status": "blocked", "reason": _redact(failure["error"])[:500]}
    if quality.get("upload_blocked"):
        return {"status": "would be blocked",
                "reason": _redact(quality.get("upload_block_reason"))[:400]}
    return {"status": "not attempted"}


def _stages(build_dir: Path) -> list[dict]:
    checkpoint = _load(build_dir / "checkpoint.json")
    stages = checkpoint.get("stages") if isinstance(checkpoint.get("stages"), dict) else {}
    result = []
    for name, record in stages.items():
        if not isinstance(record, dict):
            continue
        result.append({"stage": name, "status": record.get("status"),
                       "seconds": record.get("duration_seconds"),
                       "attempt": record.get("attempt"),
                       "error": _redact(record.get("error"))[:300] if record.get("error") else None})
    return result


# Headings used by scripts/alert_on_failure.py for the alert about a whole run.
RUN_ALERT_PREFIXES = ("❌ AI Studio ", "⚠️ AI Studio ")


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _alert_cutoff() -> datetime:
    """Notifications older than this belong to an earlier run.

    A hosted runner starts with an empty log, but a local or self-hosted
    checkout keeps ``notifications.jsonl`` between runs, and quoting last week's
    alert as today's reason would be a false statement.  ``RUN_REPORT_SINCE``
    (ISO time) sets the cutoff exactly; otherwise it is a window that is longer
    than the longest job timeout (``RUN_REPORT_ALERT_WINDOW_HOURS``, default 3).
    """
    explicit = _parse_time(os.getenv("RUN_REPORT_SINCE")) if os.getenv("RUN_REPORT_SINCE") else None
    if explicit:
        return explicit
    try:
        hours = float(os.getenv("RUN_REPORT_ALERT_WINDOW_HOURS", "") or 3)
    except ValueError:
        hours = 3.0
    return datetime.now(timezone.utc) - timedelta(hours=max(0.0, hours))


def _recent_alerts(limit: int = 3, include_info: bool = False) -> list[str]:
    """Latest warning/error notifications from this run (already redacted when
    logged).  Entries older than :func:`_alert_cutoff`, or without a readable
    time, are ignored."""
    cutoff = _alert_cutoff()
    try:
        from core import config as cfg
        path = cfg.LOG_DIR / "notifications.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    alerts = []
    for line in lines[-80:]:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = str(entry.get("message") or "")
        logged = _parse_time(entry.get("time"))
        if logged is None or logged < cutoff:
            continue
        if message.startswith(RUN_ALERT_PREFIXES):
            continue  # the run-level alert quotes this report; never quote it back
        important = message.startswith(("❌", "⚠️")) or entry.get("level") in {"error", "warning"}
        if important or (include_info and message.startswith("ℹ️")):
            alerts.append(_redact(message)[:400])
    return alerts[-limit:]


def _news_selection() -> dict:
    try:
        from core import config as cfg
        data = _load(cfg.OUTPUT_DIR / "daily_news" / "auto_selection.json")
    except Exception:
        return {}
    if not data:
        return {}
    selected = data.get("selected") or {}
    return {"candidate_count": data.get("candidate_count"),
            "feed_errors": len(data.get("feed_errors") or []),
            "selected_headline": selected.get("headline") if isinstance(selected, dict) else None,
            "clusters_considered": len(data.get("clusters_considered") or [])}


def _run_context() -> dict:
    env = os.environ
    run_id, repo, server = env.get("GITHUB_RUN_ID"), env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SERVER_URL")
    return {
        "run_id": run_id,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if run_id and repo and server else None,
        "event": env.get("GITHUB_EVENT_NAME"),
        "commit": (env.get("GITHUB_SHA") or "")[:12] or None,
        "workflow": env.get("GITHUB_WORKFLOW"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def collect(pipeline: str, job_id: int | None = None, *, job_status: str | None = None,
            artifact: str | None = None, upload_requested: bool | None = None,
            build_root=None, upload_result=None, upload_failure=None) -> dict:
    """Assemble the report.  ``job_status`` is the workflow job status so far
    (``success`` / ``failure`` / ``cancelled``); it decides between "skipped" and
    "failed" when no job was produced."""
    from core import config as cfg
    pipeline = normalise_pipeline(pipeline)
    build_root = Path(build_root) if build_root else cfg.BUILD_DIR
    job_status = str(job_status or "").strip().lower() or None
    row = find_job(pipeline, job_id, build_root=build_root)
    report = {
        "schema_version": 1,
        "generated_at": _now(),
        "pipeline": pipeline,
        "run": _run_context(),
        "workflow_job_status": job_status,
        "artifact": artifact,
        "upload_requested": upload_requested,
    }
    alerts = _recent_alerts()
    if pipeline == "news_short":
        report["news_selection"] = _news_selection()

    if row is None:
        failed = job_status in {"failure", "cancelled"}
        report.update({
            "outcome": FAILED if failed else SKIPPED,
            "job_id": None, "topic": None,
            "failure_reason": (alerts[-1] if alerts else
                               "The run stopped before a job was created; see the failing step.") if failed else None,
            "skip_reason": None if failed else (
                (_recent_alerts(include_info=True) or [None])[-1]
                or "No job was created in this run (no eligible topic or story)."),
            "upload": {"status": "not attempted"},
        })
        return report

    build_dir = build_root / f"job_{row.get('id')}"
    try:
        from core.db import normalize_format
        fmt = normalize_format(row.get("format"))
    except Exception:
        fmt = str(row.get("format") or pipeline)
    quality = _load(row.get("quality_report_path") or build_dir / "quality_report.json") or \
        _load(build_dir / "quality_report.json")
    gate = _load(build_dir / "release_gate.json")
    script = _load(build_dir / "script.json")
    if not script:
        try:
            script = json.loads(row.get("script_json") or "{}")
        except ValueError:
            script = {}
    generation = script.get("generation") if isinstance(script.get("generation"), dict) else {}
    output = _output_section(row, quality)
    upload = _upload_section(row, build_dir, gate, quality, upload_result, upload_failure)
    stages = _stages(build_dir)
    failed_stages = [item for item in stages if item.get("status") == "failed"]
    if not failed_stages and str(row.get("status")) in {"failed", "building"}:
        # A stage left "running" on an unfinished job is where the build stopped.
        failed_stages = [item for item in stages if item.get("status") == "running"]

    built = bool(output["exists"]) and str(row.get("status")) in {"done", "approved", "uploaded"}
    if upload["status"] == UPLOADED:
        outcome = UPLOADED
    elif not built or job_status == "cancelled":
        outcome = FAILED
    elif upload["status"] == "blocked":
        outcome = UPLOAD_BLOCKED
    elif job_status == "failure":
        # The video exists but a later step (upload, packaging) failed.
        outcome = UPLOAD_BLOCKED if upload_requested else FAILED
    else:
        outcome = BUILT

    reasons = []
    if row.get("error"):
        reasons.append(_redact(row["error"])[:400])
    reasons += [f"stage '{item['stage']}' did not finish" + (f": {item['error']}" if item.get("error") else "")
                for item in failed_stages]
    if outcome in {FAILED, UPLOAD_BLOCKED}:
        if upload.get("reason"):
            reasons.append(upload["reason"])
        reasons += [f"QA: {item}" for item in (quality.get("issues") or [])[:3]]
        if not reasons and alerts:
            reasons.append(alerts[-1])
        if not reasons:
            reasons.append("No failure detail was recorded; see the failing step in the run log.")

    report.update({
        "outcome": outcome,
        "job_id": row.get("id"),
        "topic": row.get("title"),
        "format": fmt,
        "language": row.get("language"),
        "category": row.get("category"),
        "job_state": row.get("status"),
        "text_model": {"provider": generation.get("provider"), "model": generation.get("model"),
                       "fallback_used": generation.get("fallback_used")} if generation else {"status": "not recorded"},
        "output": output,
        "voice": _voice_section(build_dir),
        "visuals": _visual_section(build_dir, quality),
        "research": _research_section(build_dir, fmt),
        "rights": _rights_section(build_dir, fmt),
        "qa": _qa_section(row, quality, gate),
        "upload": upload,
        "stages": stages,
        "failure_reason": "; ".join(dict.fromkeys(reasons))[:1200] if reasons and outcome in {FAILED, UPLOAD_BLOCKED} else None,
        "skip_reason": None,
    })
    return report


def _fmt_duration(seconds) -> str:
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    minutes, rest = divmod(int(round(total)), 60)
    return f"{minutes}m {rest:02d}s ({total:.1f} s)"


def _cell(value) -> str:
    text = "unknown" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def to_markdown(report: dict) -> str:
    """Render the GitHub job summary."""
    outcome = report.get("outcome")
    title = {"shorts": "Short", "longform": "Documentary", "news_short": "News Short"}.get(
        report.get("pipeline"), str(report.get("pipeline")))
    lines = [f"## {title}: {_HEADLINE.get(outcome, str(outcome).upper())}", ""]
    rows: list[tuple[str, str]] = [("Final status", _HEADLINE.get(outcome, str(outcome)))]

    if report.get("job_id") is None:
        rows.append(("Topic", "none (no job was created)"))
        selection = report.get("news_selection") or {}
        if selection:
            rows.append(("News candidates", f"{selection.get('candidate_count', 'unknown')} fresh headlines, "
                                           f"{selection.get('clusters_considered', 'unknown')} clusters considered, "
                                           f"{selection.get('feed_errors', 0)} feed errors"))
        rows.append(("Upload status", "not attempted"))
        if report.get("failure_reason"):
            rows.append(("Failure reason", report["failure_reason"]))
        if report.get("skip_reason"):
            rows.append(("Why nothing was built", report["skip_reason"]))
    else:
        output, voice, visuals = report.get("output") or {}, report.get("voice") or {}, report.get("visuals") or {}
        qa, upload, rights, research = (report.get("qa") or {}, report.get("upload") or {},
                                        report.get("rights") or {}, report.get("research") or {})
        model = report.get("text_model") or {}
        rows.append(("Topic", f"{report.get('topic')} (job #{report.get('job_id')}, {report.get('language')})"))
        rows.append(("Output file", f"{output.get('file')}" + (f", {output.get('size_mb')} MB" if output.get("size_mb") else "")
                     if output.get("exists") else "no video file was produced"))
        rows.append(("Duration", _fmt_duration(output.get("duration_seconds")) if output.get("exists") else "n/a"))
        rows.append(("Resolution", f"{output.get('width')}x{output.get('height')}"
                     if output.get("width") and output.get("height") else "unknown"))
        if model.get("model"):
            rows.append(("Script model", f"{model.get('provider')} / {model.get('model')}"
                         + (" (fallback)" if model.get("fallback_used") else "")))
        if voice.get("status") == "not recorded":
            rows.append(("Voice provider", "not recorded"))
        else:
            text = f"{voice.get('provider')} ({voice.get('voice')})"
            if voice.get("status") == "fallback":
                text += f" — FALLBACK from {voice.get('requested_provider')}"
            rows.append(("Voice provider", text))
        if visuals.get("status") == "not recorded":
            rows.append(("Visual providers", "not recorded"))
        else:
            providers = ", ".join(f"{name} x{count}" for name, count in (visuals.get("providers") or {}).items()) or "unknown"
            extra = ""
            if visuals.get("fallback_card_count"):
                extra = f" — {visuals['fallback_card_count']} of {visuals.get('asset_count')} are fallback cards"
            rows.append(("Visual providers", providers + extra))
        research_text = str(research.get("status"))
        if research.get("source_count"):
            research_text += f" ({research['source_count']} sources"
            research_text += f", {research['claim_count']} claims)" if research.get("claim_count") is not None else ")"
        rows.append(("Research status", research_text))
        rights_text = str(rights.get("status"))
        if rights.get("licence_rows") is not None:
            rights_text += f" ({rights['licence_rows']} licence rows"
            if rights.get("assets_without_provenance"):
                rights_text += f", {rights['assets_without_provenance']} assets without a recorded source"
            rights_text += ")"
        rows.append(("Rights status", rights_text))
        qa_text = f"{qa.get('status')}" + (f" ({qa.get('score')}/100)" if qa.get("score") is not None else "")
        qa_text += f"; release gate: {qa.get('release_gate')}"
        rows.append(("QA status", qa_text))
        if upload.get("status") == UPLOADED:
            verified = upload.get("privacy_verified")
            proof = ("privacy read back from YouTube" if verified else
                     "privacy NOT read back from YouTube" if verified is False else
                     "read-back not recorded")
            rows.append(("Upload status", f"uploaded as {upload.get('privacy')} — {upload.get('url')} ({proof})"))
        else:
            rows.append(("Upload status", str(upload.get("status"))
                         + (f": {upload.get('reason')}" if upload.get("reason") else "")))
        if report.get("failure_reason"):
            rows.append(("Failure reason", report["failure_reason"]))

    rows.append(("Artifact", report.get("artifact") or "none configured"))
    run = report.get("run") or {}
    if run.get("run_url"):
        rows.append(("Run", run["run_url"]))

    lines += ["| | |", "|---|---|"]
    lines += [f"| **{name}** | {_cell(value)} |" for name, value in rows]

    qa = report.get("qa") or {}
    notes = [f"- QA issue: {item}" for item in qa.get("issues") or []]
    notes += [f"- QA warning: {item}" for item in qa.get("warnings") or []]
    voice = report.get("voice") or {}
    if voice.get("fallback_reason"):
        notes.append(f"- Voice fallback: {voice['fallback_reason']}")
    if notes:
        lines += ["", "<details><summary>QA notes</summary>", "", *notes, "", "</details>"]
    stages = report.get("stages") or []
    if stages:
        lines += ["", "<details><summary>Stages</summary>", "", "| Stage | Status | Seconds |", "|---|---|---|"]
        lines += [f"| {_cell(item.get('stage'))} | {_cell(item.get('status'))} | "
                  f"{item.get('seconds') if item.get('seconds') is not None else ''} |" for item in stages]
        lines += ["", "</details>"]
    lines += ["", "_Automatic checks do not certify factual accuracy, rights or visual quality; "
                  "a private upload still needs human review before it is made public._"]
    return "\n".join(lines) + "\n"


def write(report: dict, path=None) -> Path | None:
    """Store ``run_report.json``; returns ``None`` if the location is not writable."""
    from core import config as cfg
    target = Path(path) if path else cfg.OUTPUT_DIR / "run_report.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        temporary.replace(target)
        return target
    except OSError:
        return None


def annotation(report: dict) -> str | None:
    """A single workflow command line (``::error`` / ``::warning`` / ``::notice``)."""
    outcome = report.get("outcome")

    def clean(text):
        return _redact(text).replace("%", "%25").replace("\r", " ").replace("\n", " ")[:600]

    title = {"shorts": "Short", "longform": "Documentary", "news_short": "News Short"}.get(
        report.get("pipeline"), "Run")
    if outcome == FAILED:
        return f"::error title={title} failed::{clean(report.get('failure_reason') or 'no detail recorded')}"
    if outcome == UPLOAD_BLOCKED:
        return f"::warning title={title} built but not uploaded::{clean(report.get('failure_reason') or 'upload blocked')}"
    if outcome == SKIPPED:
        return f"::notice title={title} skipped::{clean(report.get('skip_reason') or 'nothing to build')}"
    if outcome == UPLOADED:
        upload = report.get("upload") or {}
        return f"::notice title={title} uploaded (private)::{clean(upload.get('url') or '')}"
    return f"::notice title={title} built::{clean(report.get('topic') or '')}"
