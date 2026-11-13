"""Turn the build a workflow just produced into the shared review package.

    python scripts/package_build_for_review.py --mode shortform
    python scripts/package_build_for_review.py --mode daily_news --allow-no-build
    python scripts/package_build_for_review.py --mode longform

Finds the single ``build/job_<id>`` directory and its rendered MP4, calls the
packager for that mode, and writes the outcome to the run summary.  It never
uploads.  Exit codes: 0 a package awaits human review (or, with
``--allow-no-build``, nothing was built); 2 the package was written but fails
automated QA and cannot be approved; 1 no package could be built.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RELEASE_WORKFLOW = {"longform": "Release approved documentary (Private)",
                    "shortform": "Release approved Short (Private)",
                    "daily_news": "Release approved Short (Private)"}


def find_build(build_root: Path) -> list[Path]:
    if not build_root.is_dir():
        return []
    return sorted(path for path in build_root.iterdir()
                  if path.is_dir() and path.name.startswith("job_") and path.name[4:].isdigit())


def summary_markdown(mode: str, package: Path | None, summary: dict | None, message: str = "") -> str:
    lines = ["## Review package", ""]
    if summary is None:
        lines.append(message or "No review package was produced.")
        return "\n".join(lines) + "\n"
    status = summary.get("status")
    headline = {"REVIEW_REQUIRED": "Awaiting human review. Nothing was uploaded.",
                "FAIL": "NOT APPROVABLE: automated QA failed. Nothing was uploaded.",
                }.get(status, f"Unexpected status {status}.")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    lines += [f"**{headline}**", "",
              f"- Job: `{summary.get('job_id')}` ({mode})", f"- Topic: {summary.get('topic')}",
              f"- MP4 SHA-256: `{summary.get('video_sha256')}`",
              "- QA: " + ", ".join(f"{name} {state}" for name, state in (summary.get("qa_statuses") or {}).items())]
    if summary.get("errors"):
        lines += ["", "Blocking problems:"] + [f"- {item}" for item in summary["errors"][:20]]
    if status == "REVIEW_REQUIRED":
        lines += ["", "### How to review and release", "",
                  "1. Download this run's artifact and unzip it.",
                  f"2. Open `{package.name}/review_dashboard.html` in a browser; watch the video and decide every item.",
                  "3. Export the decisions from the dashboard.",
                  f"4. Run the workflow **{RELEASE_WORKFLOW[mode]}** with `source_run_id` = `{run_id}`, the "
                  "SHA-256 above, and the exported decisions.", ""]
    return "\n".join(lines) + "\n"


def write_summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", required=True, choices=sorted(RELEASE_WORKFLOW))
    parser.add_argument("--build-root", default=None, help="Default: the configured build directory")
    parser.add_argument("--output-root", default=None, help="Default: the configured output directory")
    parser.add_argument("--allow-no-build", action="store_true",
                        help="Exit 0 when nothing was built (e.g. no news story qualified today)")
    args = parser.parse_args(argv)
    from core import config as cfg
    build_root = Path(args.build_root) if args.build_root else Path(cfg.BUILD_DIR)
    output_root = Path(args.output_root) if args.output_root else Path(cfg.OUTPUT_DIR)
    builds = find_build(build_root)
    if not builds:
        message = "No video was built in this run, so there is nothing to review."
        write_summary(summary_markdown(args.mode, None, None, message))
        if args.allow_no_build:
            return 0
        print(f"::error::{message}")
        return 1
    if len(builds) != 1:
        print(f"::error::Expected exactly one build directory, found {len(builds)}: "
              + ", ".join(path.name for path in builds) + ". Refusing to guess which video to package.")
        return 1
    build = builds[0]
    job = build.name[4:]
    video = output_root / (f"longform_{job}.mp4" if args.mode == "longform" else f"shorts_{job}.mp4")
    if not video.is_file():
        message = f"The build directory {build.name} has no rendered video ({video.name}); the build did not finish."
        write_summary(summary_markdown(args.mode, None, None, message))
        print(f"::error::{message}")
        return 1
    if args.mode == "longform":
        from scripts import package_longform_review as packager
        code = packager.main(["--build-dir", str(build), "--video", str(video)])
    else:
        from scripts import package_standard_short_review as packager
        code = packager.main(["--build-dir", str(build), "--video", str(video), "--mode", args.mode])
    package = build / "shared_review_package"
    try:
        summary = json.loads((package / "package_summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = None
    write_summary(summary_markdown(args.mode, package, summary))
    if code == 2:
        print("::error::The review package fails automated QA and cannot be approved. See the run summary.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
