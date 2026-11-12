"""One upload gate for every pipeline: technical, content, source and rights QA.

Root cause addressed: only documentaries had a release gate. Shorts could be
uploaded with a FAIL quality status, a missing file, unsafe script claims or
nothing but fallback visuals, and ``run_daily`` stored documentaries as format
``long`` which skipped certification entirely.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core import config as cfg


class ReleaseBlocked(RuntimeError):
    """The job must not be uploaded; ``reasons`` lists every failed check."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("Upload blocked: " + "; ".join(self.reasons))


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _load(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def evaluate(row: dict, build_dir: Path | None = None) -> dict:
    """Run every gate and return a JSON-safe report (never raises for a failed gate)."""
    from core.db import normalize_format
    job_id = row.get("id")
    fmt = normalize_format(row.get("format"))
    build_dir = Path(build_dir) if build_dir else cfg.BUILD_DIR / f"job_{job_id}"
    checks, blocking = [], []

    def check(name, ok, detail, required=True):
        checks.append({"check": name, "status": "PASS" if ok else ("FAIL" if required else "WARN"),
                       "detail": detail})
        if not ok and required:
            blocking.append(f"{name}: {detail}")

    video = Path(str(row.get("video_path") or ""))
    exists = bool(row.get("video_path")) and video.is_file() and video.stat().st_size > 50_000
    check("video_file", exists, str(video) if exists else "rendered video is missing or empty")
    check("job_status", row.get("status") in {"done", "approved", "uploaded"},
          f"job status is '{row.get('status')}'")

    # Technical QA
    report_path = row.get("quality_report_path") or build_dir / "quality_report.json"
    quality = _load(report_path)
    status = str(quality.get("status") or row.get("quality_status") or "").upper()
    check("technical_qa", status in {"PASS", "REVIEW"},
          f"quality status is '{status or 'missing'}'"
          + (": " + "; ".join(map(str, quality.get("issues", [])[:3])) if quality.get("issues") else ""))
    if exists and quality.get("video_path"):
        # The report must describe this exact file, not an earlier render.
        same_file = Path(str(quality["video_path"])).name == video.name
        check("qa_matches_video", same_file,
              "quality report describes this file" if same_file
              else "quality report belongs to a different file")
    if quality.get("upload_blocked") and not _flag("ALLOW_FALLBACK_VISUAL_UPLOAD"):
        check("visual_fallback_share", False, str(quality.get("upload_block_reason") or
                                                  "too many fallback visuals"))

    # Content QA (Shorts scripts)
    script = {}
    try:
        script = json.loads(row.get("script_json") or "{}")
    except ValueError:
        check("script_json", False, "stored script is not valid JSON")
    if fmt in {"shorts", "news_short"} and script.get("scenes"):
        from core.content_qa import validate_shorts_script
        content = validate_shorts_script(script, row.get("language") or "english")
        check("content_qa", content["status"] != "FAIL", "; ".join(content["issues"]) or "ok")

    # Rights QA (asset provenance)
    manifest = _load(build_dir / "asset_manifest.json")
    if fmt == "shorts":
        if manifest:
            missing = manifest.get("without_provenance") or []
            check("asset_provenance", not missing or not _flag("REQUIRE_ASSET_PROVENANCE"),
                  f"{len(missing)} asset(s) have no recorded source" if missing else "all assets recorded",
                  required=_flag("REQUIRE_ASSET_PROVENANCE"))
        else:
            check("asset_provenance", False, "asset_manifest.json is missing",
                  required=_flag("REQUIRE_ASSET_PROVENANCE"))

    # Documentary certification (existing fail-closed evidence gate)
    if fmt == "longform":
        try:
            from core.release_certification import assert_release_certified
            assert_release_certified(build_dir, video)
            check("documentary_certification", True, "release certification passed")
        except Exception as exc:
            check("documentary_certification", False, str(exc)[:500])

    # News Shorts carry their own verification packet.
    if fmt == "news_short":
        verification = _load(build_dir / "verification.json")
        check("news_source_verification", verification.get("status") == "PASS",
              f"source verification is '{verification.get('status') or 'missing'}'")

    return {"job_id": job_id, "format": fmt, "status": "BLOCKED" if blocking else "PASS",
            "blocking": blocking, "checks": checks}


def assert_uploadable(row: dict, build_dir: Path | None = None) -> dict:
    report = evaluate(row, build_dir)
    try:
        target = (Path(build_dir) if build_dir else cfg.BUILD_DIR / f"job_{row.get('id')}")
        target.mkdir(parents=True, exist_ok=True)
        (target / "release_gate.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if report["status"] != "PASS":
        raise ReleaseBlocked(report["blocking"])
    return report
