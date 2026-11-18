#!/usr/bin/env python3
"""Autonomous daily news Short (see core/news_auto.py). Exit code 0 when no story qualifies."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=None, choices=["english", "hinglish", "hindi"])
    parser.add_argument("--upload", action="store_true", help="Upload the result to YouTube as PRIVATE")
    parser.add_argument("--candidates", help="Use a saved candidates JSON instead of fetching RSS feeds")
    args = parser.parse_args(argv)

    from core import news_auto, config
    from core.notify import send

    result_path = config.OUTPUT_DIR / "daily_news" / "build_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.unlink(missing_ok=True)

    def record_result(result):
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    candidates = None
    if args.candidates:
        payload = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    try:
        result = news_auto.run(language=args.language, upload=args.upload, candidates=candidates)
    except news_auto.NoPublishableStory as exc:
        message = f"Daily news Short skipped: {exc}"
        send(f"ℹ️ {message}")
        skipped = {"status": "no_story", "reason": str(exc),
                   "video_created": False, "upload_attempted": False}
        record_result(skipped)
        print(json.dumps(skipped, indent=2))
        # A skip is not a successful video build. Keep exit code 0 so the
        # workflow can still package the diagnostic/review artifact.
        print(f"::notice title=News Short not produced::{message}")
        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write(
                    "## Daily news Short: no video produced\n\n"
                    "**Status: SKIPPED — not a video-build success.**\n\n"
                    f"- Reason: {str(exc)}\n"
                    "- Script: not generated\n"
                    "- MP4: not created\n"
                    "- YouTube upload: not attempted\n"
                    "- No source-verification or editorial safety checks were bypassed.\n"
                )
        return 0
    except Exception as exc:
        send(f"❌ Daily news Short failed: {str(exc)[:400]}")
        raise
    record_result(result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["status"] == "no_story":
        send(f"ℹ️ Daily news Short skipped: {result['reason']}")
    elif result["status"] == "built":
        send(f"✅ News Short built: {result['title']}\n{result['video']}")
    elif result["status"] == "upload_blocked":
        send(f"⚠️ News Short built but NOT uploaded: {result['upload_blocked_reason'][:400]}")
        return 3  # visible in CI; artifacts are still collected
    return 0


if __name__ == "__main__":
    sys.exit(main())
