"""Human review for locally built jobs, using the same rules as the release workflows.

``python run.py upload`` and the local dashboard used to upload after an
automated gate only.  They now need what the GitHub release workflows need: a
review package for the exact MP4 and a complete set of human decisions exported
from its dashboard.  Every rule lives in ``core/review_contract.py``; this
module only finds the package of a database job and reports its state.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import config as cfg
from core import review_contract as contract

PACKAGE_DIR_NAME = "shared_review_package"
MODES = {"shorts": "shortform", "news_short": "daily_news", "longform": "longform"}


class ReviewUnavailable(RuntimeError):
    """The review package for this job could not be built."""


def mode_for(row: dict) -> str:
    from core.db import normalize_format
    fmt = normalize_format(row.get("format"))
    if fmt not in MODES:
        raise ReviewUnavailable(f"Unknown video format {row.get('format')!r}; cannot choose review rules.")
    return MODES[fmt]


def package_dir(job_id, build_dir: Path | None = None) -> Path:
    return (Path(build_dir) if build_dir else cfg.BUILD_DIR / f"job_{job_id}") / PACKAGE_DIR_NAME


def build_package(row: dict, build_dir: Path | None = None) -> dict:
    """Assemble (or rebuild) the review package of a finished local job.

    Returns the package summary plus ``package_dir`` and ``dashboard``.  A
    rebuilt package gets a new job id, so decisions made for an earlier package
    no longer apply.  Nothing is uploaded.
    """
    job_id = row.get("id")
    build = Path(build_dir) if build_dir else cfg.BUILD_DIR / f"job_{job_id}"
    video = Path(str(row.get("video_path") or ""))
    if not row.get("video_path") or not video.is_file():
        raise ReviewUnavailable(f"Job #{job_id} has no rendered video to review.")
    if not build.is_dir():
        raise ReviewUnavailable(f"The build directory of job #{job_id} is missing: {build}")
    mode = mode_for(row)
    target = package_dir(job_id, build)
    try:
        if mode == "longform":
            from scripts.package_longform_review import create_package
            create_package(build, video, target, pipeline_job_id=job_id)
        else:
            from scripts.package_standard_short_review import create_review_package
            try:
                script = json.loads(row.get("script_json") or "{}")
            except ValueError:
                script = {}
            if not script.get("scenes"):
                script = json.loads((build / "script.json").read_text(encoding="utf-8"))
            create_review_package(script, build, video=video, mode=mode, output_dir=target,
                                  pipeline_job_id=job_id, language=row.get("language"))
    except (OSError, ValueError, contract.ContractError) as exc:
        raise ReviewUnavailable(f"Could not build the review package of job #{job_id}: {exc}") from exc
    summary = json.loads((target / "package_summary.json").read_text(encoding="utf-8"))
    summary.update({"package_dir": str(target), "dashboard": str(target / "review_dashboard.html"),
                    "decisions_path": str(target / contract.DECISIONS_NAME)})
    return summary


def review_status(row: dict, build_dir: Path | None = None, *, decode: bool = True) -> dict:
    """May this job's exact MP4 be uploaded?  Never raises; ``ok`` is the answer."""
    job_id = row.get("id")
    root = package_dir(job_id, build_dir)
    hint = (f"Run 'python run.py review --id {job_id}', open the dashboard it prints, decide every item, "
            f"and save the exported decisions as {root / contract.DECISIONS_NAME}.")
    result = {"ok": False, "package_dir": str(root), "reviewer": None, "blocking": [], "detail": ""}
    if not (root / contract.MANIFEST_NAME).is_file():
        result["detail"] = "no review package exists for this video. " + hint
        return result
    decisions_path = root / contract.DECISIONS_NAME
    if not decisions_path.is_file():
        result["detail"] = "the review package has no human decisions yet. " + hint
        return result
    try:
        decisions = contract.load_decisions_text(decisions_path.read_text(encoding="utf-8"))
        mode = mode_for(row)
    except (OSError, UnicodeError, contract.ContractError, ReviewUnavailable) as exc:
        result["detail"] = f"the review decisions cannot be used: {exc}"
        return result
    video = Path(str(row.get("video_path") or ""))
    if not video.is_file():
        result["detail"] = "the rendered video is missing, so it cannot be matched to the reviewed file."
        return result
    # The file that would be uploaded must be the file that was reviewed.
    report = contract.evaluate_release(root, decisions, expected_sha256=contract.sha256_file(video),
                                       expected_mode=mode, decode=decode)
    result.update({"ok": bool(report["allowed"]), "reviewer": report.get("reviewer"),
                   "blocking": list(report["blocking"]), "video_sha256": report.get("video_sha256"),
                   "job_id": report.get("job_id")})
    if report["allowed"]:
        result["detail"] = f"every item approved by {report.get('reviewer')} for this exact file"
    else:
        shown = "; ".join(report["blocking"][:4])
        more = len(report["blocking"]) - 4
        result["detail"] = (f"human review is not complete ({len(report['blocking'])} open item(s)): {shown}"
                            + (f"; and {more} more" if more > 0 else ""))
    return result


def record_decisions(row: dict, decisions_file, build_dir: Path | None = None) -> dict:
    """Check an exported decisions file against the job's package and store it there."""
    from scripts.apply_review_decisions import apply_decisions
    root = package_dir(row.get("id"), build_dir)
    if not (root / contract.MANIFEST_NAME).is_file():
        raise ReviewUnavailable(f"Job #{row.get('id')} has no review package; run 'python run.py review --id "
                                f"{row.get('id')}' first.")
    return apply_decisions(root, decisions_file)
