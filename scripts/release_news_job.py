#!/usr/bin/env python3
"""Release one already-built daily-news Short to YouTube PRIVATE.

This is the only news path that receives YouTube credentials. It never builds,
never selects a story, and never bypasses core.news_private_release gates.
Exit 3 means the video was built but the automated release blocked it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job-id", type=int)
    selection.add_argument("--build-result", type=Path, help="Result file from this exact build run")
    args = parser.parse_args(argv)
    job_id = args.job_id
    if args.build_result:
        try:
            result = json.loads(args.build_result.read_text(encoding="utf-8"))
            if result.get("status") == "no_story":
                print(json.dumps({"status": "no_job", "reason": "This build produced no story."}))
                return 0
            job_id = result.get("job_id")
            if result.get("status") != "built" or isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
                raise ValueError("Build result does not identify a completed news job.")
        except (OSError, ValueError, AttributeError) as exc:
            print(f"Invalid build result: {exc}", file=sys.stderr)
            return 3
    if not job_id or job_id <= 0:
        parser.error("job id must be positive")
    from core.news_private_release import NewsAutoReleaseBlocked, publish_news_job
    from core.publish import DuplicateUpload
    from core.release_gate import ReleaseBlocked
    try:
        result = publish_news_job(job_id, wait=True)
    except (NewsAutoReleaseBlocked, ReleaseBlocked, DuplicateUpload) as exc:
        print(json.dumps({"status": "upload_blocked", "job_id": job_id, "reason": str(exc)[:1000]}, indent=2))
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
