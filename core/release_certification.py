"""End-to-end documentary release certification with artifact integrity hashes."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.editorial_review import assert_visual_review_ready, review_store_path

REQUIRED_REPORTS = (
    "visual_review_report.json",
    "visual_consistency_report.json",
    "audio_quality_report.json",
    "research_verification_report.json",
    "research_review_decisions.json",
    "quality_report.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def probe_release_video(video_path: str | Path) -> dict[str, Any]:
    """Inspect final MP4 streams and enforce a minimum professional delivery profile."""
    path = Path(video_path)
    if not path.is_file() or path.stat().st_size < 100_000:
        raise ValueError("Final documentary video is missing or unexpectedly small.")
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], text=True, timeout=60)
    data = json.loads(raw or "{}")
    streams = data.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    if video is None or audio is None:
        raise ValueError("Final export must contain both video and audio streams.")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width < 1280 or height < 720:
        raise ValueError(f"Final export is below 720p landscape delivery: {width}×{height}.")
    ratio = width / max(height, 1)
    if not 1.6 <= ratio <= 1.9:
        raise ValueError(f"Unexpected final aspect ratio {ratio:.3f}; expected a landscape documentary frame.")
    try:
        rate_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
        numerator, denominator = rate_text.split("/", 1)
        frame_rate = float(numerator) / max(float(denominator), 1e-9)
    except (TypeError, ValueError, ZeroDivisionError):
        frame_rate = 0.0
    if frame_rate < 20:
        raise ValueError(f"Final export frame rate is too low or unavailable: {frame_rate:.2f} fps.")
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Final export duration is missing or zero.")
    sample_rate = int(audio.get("sample_rate") or 0)
    channels = int(audio.get("channels") or 0)
    if sample_rate < 44100 or channels < 2:
        raise ValueError(
            f"Final audio delivery profile is below stereo 44.1 kHz ({sample_rate} Hz, {channels} channel(s))."
        )
    return {
        "path": str(path.resolve()), "size_bytes": path.stat().st_size,
        "duration_seconds": round(duration, 3), "width": width, "height": height,
        "aspect_ratio": round(ratio, 4), "frame_rate": round(frame_rate, 3),
        "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": sample_rate, "audio_channels": channels,
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required release artifact is missing: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Release artifact {path.name} must contain a JSON object.")
    return value


def certify_documentary(build_dir: str | Path, video_path: str | Path) -> dict[str, Any]:
    """Run every release check and persist CERTIFIED or BLOCKED evidence."""
    root = Path(build_dir).resolve()
    video = Path(video_path).resolve()
    checks, blockers, warnings = [], [], []
    reports: dict[str, dict[str, Any]] = {}

    for name in REQUIRED_REPORTS:
        try:
            reports[name] = _load_json(root / name)
            checks.append({"name": f"required_report:{name}", "status": "PASS"})
        except Exception as exc:
            blockers.append(str(exc))
            checks.append({"name": f"required_report:{name}", "status": "FAIL", "detail": str(exc)})

    visual_decisions_path = review_store_path(root / "visual_review_report.json")
    if visual_decisions_path.is_file():
        checks.append({"name": "required_report:visual_review_decisions.json", "status": "PASS"})
    else:
        blockers.append("Visual editorial decision store is missing.")
        checks.append({"name": "visual_review_decisions", "status": "FAIL"})

    review_result = None
    try:
        review_result = assert_visual_review_ready(root)
        checks.append({"name": "combined_human_review_gates", "status": "PASS"})
    except Exception as exc:
        blockers.append(str(exc))
        checks.append({"name": "combined_human_review_gates", "status": "FAIL", "detail": str(exc)})

    visual_report = reports.get("visual_review_report.json", {})
    shots = visual_report.get("shots") or []
    if not shots:
        blockers.append("Visual review manifest contains no shots.")
        checks.append({"name": "visual_manifest", "status": "FAIL"})
    else:
        checks.append({"name": "visual_manifest", "status": "PASS", "shot_count": len(shots)})
    consistency = reports.get("visual_consistency_report.json", {})
    consistency_summary = consistency.get("summary") or {}
    if consistency.get("status") not in {"PASS", "REVIEW"}:
        blockers.append("Visual consistency report is missing a valid PASS/REVIEW status.")
    if consistency_summary.get("fail_count", 0):
        blockers.append(
            f"Visual consistency report has {consistency_summary['fail_count']} severe technical failure(s)."
        )
    if consistency_summary.get("review_count", 0) or consistency_summary.get("unavailable_count", 0):
        warnings.append("Some sampled visual-quality metrics require human inspection.")
    quality = reports.get("quality_report.json", {})
    if quality.get("status") not in {"PASS", "REVIEW"}:
        blockers.append("The final video quality report is missing a valid PASS/REVIEW status.")
    elif quality.get("status") == "REVIEW":
        warnings.append("The final video quality report contains review warnings.")
    audio = reports.get("audio_quality_report.json", {})
    if audio.get("status") == "REVIEW":
        warnings.append("Audio metering reported review warnings; human listening review is recorded separately.")
    for shot in shots:
        if str(shot.get("presenter_sync_status") or "").lower() in {"fail", "error"}:
            blockers.append(f"Presenter A/V validation failed for {shot.get('shot_id')}.")
        if (shot.get("visual_quality") or {}).get("status") == "fail":
            blockers.append(f"Technical visual quality failed for {shot.get('shot_id')}.")
        if visual_report.get("semantic_screening_enabled") and (
            (shot.get("semantic_screening") or {}).get("status") != "match"
        ):
            blockers.append(f"Semantic visual screening is unresolved for {shot.get('shot_id')}.")

    video_metadata = None
    try:
        video_metadata = probe_release_video(video)
        checks.append({"name": "final_video_delivery_profile", "status": "PASS", "metadata": video_metadata})
    except Exception as exc:
        blockers.append(str(exc))
        checks.append({"name": "final_video_delivery_profile", "status": "FAIL", "detail": str(exc)})

    artifacts: dict[str, dict[str, Any]] = {}
    for path in [video, *[root / name for name in REQUIRED_REPORTS], visual_decisions_path]:
        if path.is_file():
            artifacts[str(path.resolve())] = {
                "sha256": _hash_file(path), "size_bytes": path.stat().st_size,
            }
    if video.resolve() not in [Path(item).resolve() for item in artifacts]:
        blockers.append("Final video hash could not be recorded.")

    status = "CERTIFIED" if not blockers else "BLOCKED"
    certificate = {
        "schema_version": 1,
        "certificate_id": str(uuid.uuid4()),
        "status": status,
        "created_at": _now(),
        "build_dir": str(root),
        "video_path": str(video),
        "video_metadata": video_metadata,
        "artifact_hashes": artifacts,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "human_review": {
            "combined_review_gate_passed": review_result is not None,
            "audio_review_status": audio.get("human_review_status"),
            "research_claim_count": (reports.get("research_verification_report.json", {}).get("claim_count")),
            "research_source_count": (reports.get("research_verification_report.json", {}).get("source_count")),
            "visual_shot_count": len(shots),
        },
        "limitations": [
            "Internal production release certificate only; not a legal, factual, historical or scientific certification.",
            "Hashes detect later file changes but do not establish ownership or authenticity.",
            "Human visual, rights, research and audio review attestations remain the authority.",
        ],
    }
    _atomic_json(root / "release_certificate.json", certificate)
    return certificate


def assert_release_certified(build_dir: str | Path, video_path: str | Path) -> dict[str, Any]:
    """Require a current certificate and unchanged release artifacts before publishing."""
    root = Path(build_dir).resolve()
    certificate_path = root / "release_certificate.json"
    if not certificate_path.is_file():
        raise ValueError(
            f"Release certificate is missing from {root}. Run scripts/certify_release.py or certify in the dashboard."
        )
    try:
        certificate = _load_json(certificate_path)
    except Exception as exc:
        raise ValueError(f"Cannot read release certificate: {exc}") from exc
    if certificate.get("status") != "CERTIFIED":
        raise ValueError("Release certificate status is BLOCKED; resolve every listed blocker and recertify.")
    if str(Path(video_path).resolve()) != certificate.get("video_path"):
        raise ValueError("The video path differs from the certified release artifact.")
    recorded = certificate.get("artifact_hashes") or {}
    required_paths = {
        str((root / name).resolve()) for name in REQUIRED_REPORTS
    } | {
        str(review_store_path(root / "visual_review_report.json").resolve()),
        str(Path(video_path).resolve()),
    }
    if not required_paths.issubset(set(recorded)):
        raise ValueError("Release certificate is missing one or more required artifact hashes; recertify.")
    for artifact_path, expected in recorded.items():
        path = Path(artifact_path)
        if not path.is_file() or _hash_file(path) != expected.get("sha256"):
            raise ValueError(f"Certified artifact changed or is missing: {path.name}. Re-run release certification.")
    assert_visual_review_ready(root)
    return certificate
