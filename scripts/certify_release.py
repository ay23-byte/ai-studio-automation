"""Run documentary release certification for a completed build."""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json

from core.release_certification import certify_documentary


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify a completed long-form documentary release.")
    parser.add_argument("--build-dir", required=True, help="Build directory containing review and QA reports.")
    parser.add_argument("--video", required=True, help="Final rendered MP4 path.")
    args = parser.parse_args()
    certificate = certify_documentary(args.build_dir, args.video)
    print(json.dumps(certificate, ensure_ascii=False, indent=2))
    return 0 if certificate["status"] == "CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
