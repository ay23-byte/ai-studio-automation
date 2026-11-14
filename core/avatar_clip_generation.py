"""Create chapter-aligned 3D presenter clips with the repository Avatar Studio.

Requires Node.js, FFmpeg and the pinned packages in avatar_studio/package.json
(`npm ci` in avatar_studio, then `npx playwright install chromium`). Nothing is fetched
from a CDN while rendering.

Each clip is a short chapter introduction; documentary B-roll carries the remaining narration.
Clips are rendered offline, one frame at a time, by avatar_studio/render-offline.mjs, so the
frame rate does not depend on how fast the runner is. Every clip is then measured: a clip
whose picture is frozen, repeated, out of step with its narration or has a still mouth is
rejected here, with the reason, instead of reaching the edit.
"""
import json
import os
import subprocess
from pathlib import Path

from core.config import ROOT
from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin, render_timeout, tool_timeout


class PresenterRenderError(RuntimeError):
    """The 3D presenter could not be rendered, or what was rendered is not usable."""


def _last_lines(*streams, limit=6, width=700):
    lines = []
    for stream in streams:
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", "replace")
        lines.extend(line.strip() for line in str(stream or "").splitlines() if line.strip())
    errors = [line for line in lines if "::error" in line or "Error" in line] or lines
    text = " | ".join(line.split("::", 2)[-1] if line.startswith("::error") else line for line in errors[-limit:])
    return text[-width:] if text else "The renderer printed no message."


def _media_seconds(path):
    probe = subprocess.run([
        ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)
    ], capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    try:
        return float(probe.stdout.strip())
    except ValueError as exc:
        raise PresenterRenderError(f"Could not read the duration of {Path(path).name}.") from exc


def presenter_intro_duration(section_duration, max_seconds=8.0):
    """Keep the on-camera presenter to a brief intro, never the whole chapter."""
    section_duration = max(0.0, float(section_duration))
    max_seconds = max(3.0, min(float(max_seconds), 12.0))
    if section_duration <= 0:
        raise ValueError("Chapter duration must be positive.")
    return min(section_duration, max(5.0, min(max_seconds, section_duration * 0.15)))


def generate_chapter_avatar_clips(voice_path, sections, voice_gender, build_dir):
    """Split narration at chapter boundaries and record a matching 3D avatar clip."""
    voice_path = Path(voice_path)
    output_dir = ROOT / "assets" / "presenter_clips"
    audio_dir = Path(build_dir) / "avatar_chapter_audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Remove numbered clips from previous runs. Otherwise a partial new run can
    # appear complete because stale chapter MP4s remain in the shared directory.
    for stale in output_dir.glob("presenter_section_*"):
        if stale.is_file() and (stale.suffix.lower() in {".mp4", ".webm", ".mov"}
                                or stale.name.endswith((".source.json", ".render.json"))):
            stale.unlink()

    for index, section in enumerate(sections, start=1):
        audio_path = audio_dir / f"chapter_{index:02d}.wav"
        start = float(section["_start"])
        chapter_duration = float(section["_dur"])
        try:
            max_intro = float(os.getenv("PRESENTER_INTRO_SECONDS", "8"))
        except ValueError:
            max_intro = 8.0
        duration = presenter_intro_duration(chapter_duration, max_intro)
        subprocess.run([
            ffmpeg_bin(), "-y", "-ss", f"{start:.3f}", "-i", str(voice_path),
            "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "24000",
            "-c:a", "pcm_s16le", str(audio_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=render_timeout())
        if not audio_path.is_file() or audio_path.stat().st_size < 1024:
            raise RuntimeError(f"Chapter {index} narration slice is missing or too small.")

    env = os.environ.copy()
    env["AVATAR_AUDIO_DIR"] = str(audio_dir.resolve())
    env["AVATAR_OUTPUT_DIR"] = str(output_dir.resolve())
    env["AVATAR_GENDER"] = str(voice_gender or "female").lower()
    env["FFMPEG_BINARY"] = ffmpeg_bin()
    env["FFPROBE_BINARY"] = ffprobe_bin()
    studio = ROOT / "avatar_studio"
    if not (studio / "node_modules" / "three" / "package.json").is_file() and not env.get("AVATAR_NODE_MODULES"):
        raise PresenterRenderError(
            "The 3D presenter's browser packages are not installed. Run "
            "`npm ci` and `npx playwright install chromium` in avatar_studio, "
            "or choose another presenter mode."
        )
    command = ["node", str(studio / "render-offline.mjs"), str(len(sections))]
    try:
        run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=tool_timeout())
    except FileNotFoundError as exc:
        raise PresenterRenderError("Node.js is not installed, so the 3D presenter cannot be rendered.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PresenterRenderError(
            f"The 3D presenter render did not finish within {tool_timeout()} seconds. "
            + _last_lines(exc.stdout, exc.stderr)
        ) from exc
    if run.stdout:
        print(run.stdout.rstrip())
    if run.returncode != 0:
        # The renderer's own message is the useful part; without it the run report only
        # says that a command failed.
        raise PresenterRenderError(
            f"The 3D presenter render failed (exit {run.returncode}). " + _last_lines(run.stdout, run.stderr)
        )

    from core.presenter_quality import analyse_presenter_motion, inspect_presenter_clip, write_presenter_metadata
    for index, section in enumerate(sections, start=1):
        clip = output_dir / f"presenter_section_{index:02d}.mp4"
        if not clip.is_file() or clip.stat().st_size < 10000:
            raise PresenterRenderError(f"3D avatar generation did not produce chapter {index}: {clip}")
        narration = _media_seconds(audio_dir / f"chapter_{index:02d}.wav")
        render = {}
        record = clip.with_suffix(".render.json")
        if record.is_file():
            render = json.loads(record.read_text(encoding="utf-8"))
        streams = inspect_presenter_clip(clip, expected_duration=narration, tolerance_seconds=0.25,
                                         require_audio=True, ffprobe=ffprobe_bin())
        motion = analyse_presenter_motion(clip, mouth_box=render.get("mouth_box"),
                                          mouth_track=render.get("mouth_open_per_frame"),
                                          ffmpeg=ffmpeg_bin(), ffprobe=ffprobe_bin())
        write_presenter_metadata(clip, {
            "renderer": render.get("renderer") or "unknown",
            "render": {key: value for key, value in render.items() if key != "mouth_open_per_frame"},
            "narration_segment_seconds": round(narration, 3),
            "validation": streams, "motion": motion,
            "human_visual_review": "pending",
        })
        problems = list(streams["issues"]) + list(motion["issues"])
        if problems:
            raise PresenterRenderError(f"3D presenter clip for chapter {index} was rejected: " + "; ".join(problems))
        print(f"Presenter chapter {index}: {motion['distinct_frame_rate']:.1f} different frames per second, "
              f"longest freeze {motion['longest_frozen_seconds']:.2f}s, mouth moving in "
              f"{(motion.get('voiced_mouth_motion_ratio') or 0) * 100:.0f}% of voiced frames")
    return output_dir
