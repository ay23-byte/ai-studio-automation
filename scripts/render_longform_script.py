"""Re-render a saved long-form script without regenerating its narration/storyboard.

Example:
  WAN_MAPPED_REVIEW_REPORT=build/job_42/wan_mapped/visual_review_report.json \
    python scripts/render_longform_script.py --script build/job_42/script.json --job-id 42
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.config import BUILD_DIR
from core.pipeline import build_longform


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="Exact script.json from the original build")
    parser.add_argument("--job-id", required=True, type=int, help="Original long-form job ID")
    parser.add_argument("--language", default="english")
    parser.add_argument("--voice-gender", choices=("male", "female"), default="male")
    args = parser.parse_args()

    script_path = Path(args.script)
    if not script_path.is_file():
        raise FileNotFoundError(f"Saved script not found: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if not isinstance(script, dict) or not isinstance(script.get("sections"), list) or not script["sections"]:
        raise ValueError("Saved script must be a JSON object with non-empty sections.")

    build_dir = BUILD_DIR / f"job_{args.job_id}"
    output = build_longform(
        script, args.job_id, build_dir=build_dir,
        language=args.language, voice_gender=args.voice_gender,
    )
    print(f"Re-rendered from saved script: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
