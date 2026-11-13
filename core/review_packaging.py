"""Helpers shared by the three review-package builders.

The builders (``scripts/package_longform_review.py``,
``scripts/package_standard_short_review.py`` and
``scripts/package_daily_news_review.py``) translate what a pipeline recorded
into the shared manifest.  They translate; they never invent evidence: a
missing source URL stays missing, an unknown origin stays ``unknown``, and a
prompt that was not retained is recorded as absent.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from core import review_contract as contract

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
#: Engines that are a last-resort synthetic voice, never an intended narrator.
ROBOTIC_ENGINES = {"espeak", "espeak-ng", "tone", "silence"}


def load_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {} if default is None else default
    return value if isinstance(value, dict) else ({} if default is None else default)


def load_sidecar(media_path) -> dict:
    """The ``<file>.source.json`` record written when the asset was acquired."""
    media_path = Path(media_path)
    return load_json(media_path.with_suffix(media_path.suffix + ".source.json"))


def asset_type_of(path) -> str:
    return "video" if Path(path).suffix.lower() in VIDEO_SUFFIXES else "image"


def describe_asset(metadata: dict, *, prompt: str = "", licence_row: dict | None = None) -> tuple[dict, dict]:
    """Map an acquisition record to ``(provenance, rights)`` for the manifest.

    ``prompt`` is used only for AI-generated assets, and only when it is the
    real prompt.  ``licence_row`` is an optional ``licenses.csv`` row.
    """
    metadata = metadata if isinstance(metadata, dict) else {}
    row = licence_row if isinstance(licence_row, dict) else {}
    provider = str(metadata.get("provider") or row.get("source") or "").strip()
    kind = str(metadata.get("asset_type") or "").lower()
    source_url = str(metadata.get("page_url") or metadata.get("source_url") or metadata.get("url")
                     or row.get("url") or "").strip()
    is_ai = bool(metadata.get("ai_generated")) or "ai_generated" in kind or provider.lower() == "pollinations"
    is_graphic = not is_ai and ("programmatic_graphic" in kind or "project_graphic" in kind)
    provenance = {
        "provider": provider or None,
        "title": str(metadata.get("title") or "").strip() or None,
        "creator": str(metadata.get("creator") or metadata.get("author") or row.get("author") or "").strip() or None,
        "source_url": source_url or None,
        "download_url": str(metadata.get("download_url") or metadata.get("media_url") or "").strip() or None,
        "date_accessed": metadata.get("date_accessed") or row.get("date_accessed") or None,
    }
    if is_ai:
        provenance["origin"] = "ai_generated"
        provenance["prompt"] = str(metadata.get("prompt") or prompt or "").strip() or None
        provenance["model"] = metadata.get("model") or None
    elif is_graphic:
        provenance["origin"] = "project_graphic"
        provenance["fallback"] = bool(metadata.get("fallback"))
        provenance["graphic_kind"] = metadata.get("graphic_kind") or metadata.get("fallback_kind") or None
    elif provider and source_url:
        provenance["origin"] = "source"
    else:
        provenance["origin"] = "unknown"
    licence = str(metadata.get("licence") or metadata.get("license") or row.get("licence") or "").strip()
    rights = {
        "status": "unreviewed",
        "license": licence or None,
        "license_url": str(metadata.get("licence_url") or metadata.get("license_url")
                           or row.get("licence_url") or "").strip() or None,
        "attribution": str(metadata.get("attribution_text") or metadata.get("attribution")
                           or row.get("attribution_text") or "").strip() or None,
        "usage_scope": metadata.get("usage_scope") or None,
        "licence_document_ref": metadata.get("licence_document_ref") or None,
    }
    return provenance, rights


def copy_into(source, root, relative) -> str:
    """Copy ``source`` to ``root/relative`` and return the POSIX relative path."""
    target = contract.inside(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(source).resolve() != target:
        shutil.copy2(source, target)
    return Path(relative).as_posix()


def write_qa(root, name: str, status: str, details, **extra) -> None:
    if name not in contract.QA_FILES:
        raise ValueError(f"Unknown QA report: {name}")
    if status not in {"PASS", "REVIEW", "FAIL"}:
        raise ValueError(f"Invalid QA status: {status}")
    if isinstance(details, str):
        details = [details]
    contract.write_json(Path(root) / contract.QA_FILES[name],
                        {"status": status, "details": [item for item in details if item], **extra})


def technical_report(root, mode: str, video_path, extra_failures=()) -> tuple[str, list]:
    """Probe, profile-check and fully decode the packaged MP4."""
    failures = [str(item) for item in extra_failures if item]
    details = []
    try:
        probe = contract.probe_video(video_path)
    except contract.ContractError as exc:
        return "FAIL", [str(exc)] + failures
    details.append(f"{probe['width']}x{probe['height']}, {probe['duration_seconds']} s, {probe['fps']} fps, "
                   f"{probe['video_codec']}/{probe['audio_codec']}, {probe['audio_channels']} audio channel(s).")
    failures.extend(contract.profile_problems(mode, probe))
    decoded = contract.decode_check(video_path, probe["duration_seconds"])
    if decoded["decoded"]:
        details.append(decoded["detail"])
    else:
        failures.append(decoded["detail"])
    return ("FAIL" if failures else "PASS"), details + failures


def narration_metadata(build_dir) -> dict:
    """What actually spoke the narration, including any fallback."""
    report = load_json(Path(build_dir) / "narration_synthesis_report.json")
    if not report:
        return {}
    requested, actual = report.get("requested_engine"), report.get("actual_engine")
    reason = report.get("fallback_reason")
    return {
        "provider": actual, "requested_provider": requested, "voice": report.get("voice"),
        "language": report.get("language"), "speech_seconds": report.get("speech_seconds"),
        "fallback_used": bool(reason or (requested and actual and requested != actual)),
        "fallback_reason": str(reason)[:400] if reason else None,
    }


def audio_status(build_dir, narration: dict | None = None) -> tuple[str, list]:
    """Audio QA: metering can fail the package; it can never approve it."""
    report = load_json(Path(build_dir) / "audio_quality_report.json")
    narration = narration if narration is not None else narration_metadata(build_dir)
    details, status = [], "REVIEW"
    if report:
        measured = str(report.get("status") or "").upper()
        details.append(f"Automated audio metering: {measured or 'no status'}.")
        for issue in (report.get("issues") or [])[:8]:
            details.append(str(issue.get("message") if isinstance(issue, dict) else issue)[:300])
        if measured == "FAIL":
            status = "FAIL"
    else:
        details.append("No automated audio metering report was produced for this build.")
    if narration:
        details.append(f"Narration engine: {narration.get('provider')} (requested {narration.get('requested_provider')}), "
                       f"voice {narration.get('voice')}, language {narration.get('language')}.")
        engine = str(narration.get("provider") or "").lower()
        if narration.get("fallback_used") and engine in ROBOTIC_ENGINES:
            status = "FAIL"
            details.append("The configured narrator was unavailable and a robotic fallback voice was substituted "
                           f"({narration.get('fallback_reason') or 'no reason recorded'}). This is not a "
                           "publishable narration; rebuild when the voice provider is available.")
        elif narration.get("fallback_used"):
            details.append("A fallback voice provider was used: " + str(narration.get("fallback_reason") or ""))
    details.append("A human must listen to the complete narration and mix.")
    return status, details


def script_metadata(script: dict) -> dict:
    generation = script.get("generation") if isinstance(script.get("generation"), dict) else {}
    return {"provider": generation.get("provider"), "model": generation.get("model"),
            "fallback_used": bool(generation.get("fallback_used")),
            "attempted_models": generation.get("attempted_models") or []}


def publish_metadata(title, description, tags) -> dict:
    """Title, description and tags the release will use (fixed at build time)."""
    clean_tags = [str(tag).strip() for tag in (tags or []) if isinstance(tag, (str, int)) and str(tag).strip()]
    return {"title": " ".join(str(title or "").split())[:100],
            "description": str(description or "").strip()[:4500],
            "tags": clean_tags[:30]}
