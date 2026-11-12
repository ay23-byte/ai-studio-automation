#!/usr/bin/env python3
"""Autonomous daily news Short (see core/news_auto.py). Exit code 0 when no story qualifies."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default=None, choices=["english", "hinglish", "hindi"])
    parser.add_argument("--upload", action="store_true", help="Upload the result to YouTube as PRIVATE")
    parser.add_argument("--candidates", help="Use a saved candidates JSON instead of fetching RSS feeds")
    args = parser.parse_args(argv)

    from core import news_auto
    from core.notify import send
    candidates = None
    if args.candidates:
        payload = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        candidates = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    try:
        result = news_auto.run(language=args.language, upload=args.upload, candidates=candidates)
    except news_auto.NoPublishableStory as exc:
        send(f"ℹ️ Daily news Short skipped: {exc}")
        print(json.dumps({"status": "no_story", "reason": str(exc)}, indent=2))
        return 0
    except Exception as exc:
        send(f"❌ Daily news Short failed: {str(exc)[:400]}")
        raise
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
