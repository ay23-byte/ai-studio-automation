"""Release the single daily-news Short produced by one exact build run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core import config as cfg
    from core.db import get_video, list_videos
    from core.news_private_release import publish_news_job

    candidates = []
    for row in list_videos(limit=1000):
        fmt = str(row.get("format") or "").lower().replace("-", "_")
        if fmt not in {"news_short", "daily_news"}:
            continue
        job_id = int(row["id"])
        build_dir = Path(cfg.BUILD_DIR) / f"job_{job_id}"
        video_path = Path(str(row.get("video_path") or ""))
        if (build_dir / "verification.json").is_file() and video_path.is_file():
            candidates.append(job_id)

    if not candidates:
        result = {"status": "skipped", "reason": "This run produced no eligible daily-news MP4; nothing to upload."}
        print(json.dumps(result, indent=2))
        summary = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("## Daily news private release\n\nNo eligible MP4 was produced. Nothing was uploaded.\n")
        return 0
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one daily-news job in this run's artifact; found {candidates}.")

    result = publish_news_job(candidates[0], wait=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "uploaded" or result.get("privacy") != "private" or not result.get("privacy_verified"):
        raise RuntimeError("YouTube did not confirm a successful PRIVATE upload.")
    summary = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"## Daily news private release\n\nUploaded and verified PRIVATE: {result.get('url')}\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"::error::Daily news private release failed: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
