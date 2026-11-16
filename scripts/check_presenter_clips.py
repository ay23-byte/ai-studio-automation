#!/usr/bin/env python3
"""Measure rendered presenter clips and report what a viewer would see.

    python scripts/check_presenter_clips.py <folder with presenter_section_NN.mp4> [--report out.json]

For every clip: stream and duration checks, the number of frames that are really different,
the longest freeze, and whether the mouth moves while narration is audible. A contact sheet
of the face is written beside each clip for the human reviewer. Exits 1 if any clip fails.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin  # noqa: E402
from core.presenter_quality import (  # noqa: E402
    analyse_presenter_motion, inspect_presenter_clip, read_presenter_metadata, read_render_record,
)


def contact_sheet(clip: Path, frames: int, target: Path) -> bool:
    """Twelve evenly spaced frames in one picture, for a quick look at face and mouth."""
    step = max(1, frames // 12)
    run = subprocess.run([
        ffmpeg_bin(), "-y", "-v", "error", "-i", str(clip),
        "-vf", f"select='not(mod(n\\,{step}))',scale=480:-2,tile=4x3", "-frames:v", "1", "-q:v", "3", str(target),
    ], capture_output=True, timeout=PROBE_TIMEOUT * 8)
    return run.returncode == 0 and target.is_file()


def check_folder(folder: Path) -> dict:
    clips = sorted(folder.glob("presenter_section_*.mp4"))
    report = {"folder": str(folder), "clips": [], "status": "FAIL" if not clips else "PASS"}
    if not clips:
        report["issues"] = [f"No presenter_section_NN.mp4 files in {folder}."]
        return report
    for clip in clips:
        render = read_render_record(clip)
        expected = render.get("narration_seconds") or read_presenter_metadata(clip).get("narration_segment_seconds")
        streams = inspect_presenter_clip(clip, expected_duration=expected, tolerance_seconds=0.25,
                                         require_audio=True, ffprobe=ffprobe_bin())
        motion = analyse_presenter_motion(clip, mouth_box=render.get("mouth_box"),
                                          mouth_track=render.get("mouth_open_per_frame"),
                                          ffmpeg=ffmpeg_bin(), ffprobe=ffprobe_bin())
        sheet = clip.with_suffix(".contact.jpg")
        entry = {
            "clip": clip.name,
            "status": "FAIL" if "FAIL" in (streams["status"], motion["status"]) else (
                "REVIEW" if "REVIEW" in (streams["status"], motion["status"]) else "PASS"),
            "width": streams["width"], "height": streams["height"], "frame_rate": streams["frame_rate"],
            "render_seconds": render.get("render_seconds"), "webgl_renderer": render.get("webgl_renderer"),
            "issues": streams["issues"] + motion["issues"], "warnings": streams["warnings"] + motion["warnings"],
            "streams": streams, "motion": motion,
            "contact_sheet": sheet.name if contact_sheet(clip, int(motion.get("frames_analysed") or 12), sheet) else None,
            "human_visual_review": "pending",
        }
        report["clips"].append(entry)
        if entry["status"] == "FAIL":
            report["status"] = "FAIL"
        elif entry["status"] == "REVIEW" and report["status"] == "PASS":
            report["status"] = "REVIEW"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = check_folder(args.folder)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for issue in report.get("issues", []):
        print(f"::error title=Presenter clips::{issue}")
    for entry in report["clips"]:
        motion = entry["motion"]
        line = (
            f"{entry['clip']}: {entry['status']} | {entry['width']}x{entry['height']} at {entry['frame_rate']} fps | "
            f"{motion.get('distinct_frames')} different frames of {motion.get('frames_analysed')} "
            f"({motion.get('distinct_frame_rate')} per second) | longest freeze {motion.get('longest_frozen_seconds')}s | "
            f"mouth moving in {round((motion.get('voiced_mouth_motion_ratio') or 0) * 100)}% of voiced frames | "
            f"picture-to-mouth-track offset {motion.get('mouth_track_offset_frames')} frames | "
            f"rendered in {entry['render_seconds']}s"
        )
        level = "error" if entry["status"] == "FAIL" else "notice"
        print(f"::{level} title=Presenter clip {entry['clip']}::{line}")
        for problem in entry["issues"]:
            print(f"::error title=Presenter clip {entry['clip']}::{problem}")
        for warning in entry["warnings"]:
            print(f"::warning title=Presenter clip {entry['clip']}::{warning}")
    print(f"Presenter clips: {report['status']} ({len(report['clips'])} checked). Human visual review is still required.")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
