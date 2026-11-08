"""Create chapter-aligned 3D presenter clips with the repository Avatar Studio.

Requires Node.js, Playwright Chromium, FFmpeg, and network access to the SDK CDN.
Each clip is a short chapter introduction; documentary B-roll carries the remaining narration.
"""
import os
import subprocess
from pathlib import Path

from core.config import ROOT


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
        if stale.is_file() and stale.suffix.lower() in {".mp4", ".webm", ".mov"}:
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
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(voice_path),
            "-t", f"{duration:.3f}", "-vn", "-ac", "1", "-ar", "24000",
            "-c:a", "pcm_s16le", str(audio_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not audio_path.is_file() or audio_path.stat().st_size < 1024:
            raise RuntimeError(f"Chapter {index} narration slice is missing or too small.")

    env = os.environ.copy()
    env["AVATAR_AUDIO_DIR"] = str(audio_dir.resolve())
    env["AVATAR_OUTPUT_DIR"] = str(output_dir.resolve())
    env["AVATAR_GENDER"] = str(voice_gender or "female").lower()
    runner = ROOT / "avatar_studio" / "generate-clips.mjs"
    subprocess.run(["node", str(runner), str(len(sections))], cwd=ROOT, env=env, check=True)

    # Require every generated chapter clip and verify it has decodable streams.
    for index, section in enumerate(sections, start=1):
        clip = output_dir / f"presenter_section_{index:02d}.mp4"
        if not clip.is_file() or clip.stat().st_size < 10000:
            raise RuntimeError(f"3D avatar generation did not produce chapter {index}: {clip}")
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(clip)
        ], check=True, capture_output=True, text=True)
        streams = set(probe.stdout.splitlines())
        if not {"video", "audio"}.issubset(streams):
            raise RuntimeError(f"3D avatar chapter {index} must contain both audio and video.")
    return output_dir
