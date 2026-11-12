#!/usr/bin/env python3
"""Send one failure notification that says *why* the run failed.

Runs as the last step of a production workflow with ``if: failure()``.  It reads
``output/run_report.json`` (written by ``scripts/write_job_summary.py``) so the
message carries the outcome and the reason instead of only a link.  It never
fails the job further and never prints credentials.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_message(name: str, report: dict | None) -> str:
    server, repo, run_id = (os.getenv("GITHUB_SERVER_URL"), os.getenv("GITHUB_REPOSITORY"),
                            os.getenv("GITHUB_RUN_ID"))
    url = f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""
    report = report or {}
    outcome = report.get("outcome")
    if outcome == "upload_blocked":
        head = f"⚠️ AI Studio {name}: video built but NOT uploaded"
    elif outcome == "uploaded":
        # The video is on YouTube (private); a later step failed.
        head = f"⚠️ AI Studio {name}: uploaded, but a later step failed"
    else:
        head = f"❌ AI Studio {name} failed"
    parts = [head]
    if report.get("topic"):
        parts.append(f"Topic: {report['topic']} (job #{report.get('job_id')})")
    reason = report.get("failure_reason")
    if reason:
        parts.append(f"Reason: {str(reason)[:600]}")
    elif not report:
        parts.append("No run report was written; see the failing step.")
    upload = report.get("upload") or {}
    if upload.get("url"):
        parts.append(f"Video: {upload['url']} ({upload.get('privacy') or 'privacy unknown'})")
    if url:
        parts.append(url)
    return "\n".join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", required=True, help="Workflow name used in the message")
    parser.add_argument("--report", default=None, help="Path to run_report.json")
    args = parser.parse_args(argv)
    try:
        from core import config as cfg
        from core.notify import send
        path = Path(args.report) if args.report else cfg.OUTPUT_DIR / "run_report.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        send(build_message(args.name, report if isinstance(report, dict) else None))
    except Exception as exc:  # an alert problem must not hide the original failure
        print(f"WARNING: failure alert could not be sent: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
