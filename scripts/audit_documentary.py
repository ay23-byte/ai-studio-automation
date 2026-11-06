"""CLI for the documentary workflow audit.

Usage:
  python scripts/audit_documentary.py --input documentary_audit_input.json
"""
import argparse
import json
import sys
from pathlib import Path

# When executed as `python scripts/audit_documentary.py`, Python places the
# scripts directory (not the repository root) on sys.path. Add the root so
# the sibling `core` package is importable in GitHub Actions and local runs.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from core.workflow_audit import build_workflow_report, save_workflow_report


def main():
    parser = argparse.ArgumentParser(description="Audit documentary research, scenes, footage and presenter timing.")
    parser.add_argument("--input", required=True, help="JSON file containing research/scenes/assets/chapters.")
    parser.add_argument("--video", help="Optional rendered MP4 to inspect with FFmpeg/FFprobe.")
    parser.add_argument("--subtitles", help="Optional subtitle file to inspect with the rendered MP4.")
    parser.add_argument("--output", default="output/workflow_audit.json", help="Report JSON destination.")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = build_workflow_report(
        research=data.get("research"),
        scenes=data.get("scenes"),
        assets=data.get("assets"),
        chapters=data.get("chapters"),
        video_path=args.video,
        subtitle_path=args.subtitles,
    )
    path = save_workflow_report(report, args.output)
    print(json.dumps(report["summary"], indent=2))
    print(f"Audit report saved: {path}")
    return 1 if report["summary"]["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
