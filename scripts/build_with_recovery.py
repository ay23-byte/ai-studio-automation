#!/usr/bin/env python3
"""Run a build command; if it fails part-way, resume the job once from its checkpoint.

    python scripts/build_with_recovery.py -- python run_daily.py --format shorts
    python scripts/build_with_recovery.py --upload-after-recovery -- \\
        python scripts/run_daily_news_short.py --upload

What happens when the command exits non-zero:

* exit code 3 (built, upload blocked) and other deliberate outcomes are passed
  through unchanged;
* if the run created a job that failed for a possibly transient reason, the job
  is resumed from its checkpoint (finished stages are reused);
* if no job was created (for example every text model was busy), the command is
  run again once after a pause;
* configuration, credential, content-gate and quality-gate failures are never
  retried, because they would fail the same way.

The attempt count is bounded by ``BUILD_RECOVERY_ATTEMPTS`` (default 1, 0 turns
recovery off).  The original exit code is returned when recovery is not possible.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS_THROUGH_EXIT_CODES = {3}   # built, upload blocked: nothing to recover


def _delay() -> float:
    try:
        return max(0.0, float(os.getenv("BUILD_RECOVERY_DELAY_SECONDS", "30") or 30))
    except ValueError:
        return 30.0


def _note(message: str, level: str = "notice") -> None:
    print(f"::{level} title=Build recovery::{message}" if os.getenv("GITHUB_ACTIONS") else f"[recovery] {message}",
          flush=True)


def _run(command: list[str]) -> tuple[int, str]:
    """Run ``command`` with live output; return the exit code and the output tail."""
    tail: list[str] = []
    process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line)
        if len(tail) > 60:
            del tail[0]
    return process.wait(), "".join(tail)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--upload-after-recovery", action="store_true",
                        help="Publish privately (through the release gate) after a successful resume")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- followed by the build command")
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a build command is required after --")

    from core import recovery
    before = recovery.job_ids()
    code, tail = _run(command)
    if code == 0 or code in PASS_THROUGH_EXIT_CODES:
        return code

    attempts = recovery.max_attempts()
    for attempt in range(1, attempts + 1):
        failed = recovery.find_failed_job(before)
        if failed is not None:
            ok, reason = recovery.can_resume(failed)
            if not ok:
                _note(f"Job #{failed['id']} failed and will not be retried ({reason}).", "error")
                return code
            _note(f"Job #{failed['id']} failed ({reason}); resuming from its checkpoint "
                  f"(attempt {attempt}/{attempts}) in {_delay():.0f}s.", "warning")
            time.sleep(_delay())
            try:
                out = recovery.resume(failed)
            except BaseException as exc:  # the resumed build records its own error
                _note(f"Resume of job #{failed['id']} failed: {type(exc).__name__}: {str(exc)[:300]}", "error")
                if isinstance(exc, KeyboardInterrupt):
                    raise
                continue
            _note(f"Job #{failed['id']} recovered: {Path(out).name}")
            if args.upload_after_recovery:
                return _publish(int(failed["id"]))
            return 0
        if recovery.created_jobs(before):
            # A job exists and is not failed: the failure happened after the build.
            _note("The build finished but a later step of the command failed; not rebuilding.", "error")
            return code
        retryable, reason = recovery.classify_error(_last_error_line(tail))
        if not retryable:
            _note(f"The command failed before a job was created and will not be retried ({reason}).", "error")
            return code
        _note(f"The command failed before a job was created; running it again "
              f"(attempt {attempt}/{attempts}) in {_delay():.0f}s.", "warning")
        time.sleep(_delay())
        code, tail = _run(command)
        if code == 0 or code in PASS_THROUGH_EXIT_CODES:
            return code
    return code or 1


def _last_error_line(tail: str) -> str:
    """The exception line of a traceback (``Type: message``), if there is one."""
    for line in reversed(tail.splitlines()):
        text = line.strip()
        if text and not text.startswith(("File ", "raise ", "^", "NOTIFY:")) and ":" in text:
            head = text.split(":", 1)[0]
            name = head.rsplit(".", 1)[-1]
            if name.isidentifier() and (name.endswith(("Error", "Exception", "Exit", "Interrupt"))
                                        or name in {"NoPublishableStory", "ReleaseBlocked", "DuplicateUpload",
                                                    "FFmpegUnavailable", "VoiceProviderUnavailable"}):
                return f"{name}:{text.split(':', 1)[1]}"
    return ""


def _publish(job_id: int) -> int:
    from core.notify import send
    from core.publish import DuplicateUpload, publish_job
    from core.release_gate import ReleaseBlocked
    try:
        result = publish_job(job_id, wait=True)
    except (ReleaseBlocked, DuplicateUpload) as exc:
        send(f"⚠️ Recovered job #{job_id} was built but NOT uploaded: {str(exc)[:400]}")
        return 3
    print(f"Uploaded recovered job #{job_id}: {result.get('url') or result.get('youtube_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
