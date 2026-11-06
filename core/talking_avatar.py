"""Resolve pre-rendered Talking Avatar clips for long-form chapters."""
import os
import subprocess
from pathlib import Path

from core.config import ROOT


def clips_dir():
    configured = os.getenv("AVATAR_CLIPS_DIR", "assets/presenter_clips")
    path = Path(configured).expanduser()
    return path if path.is_absolute() else ROOT / path


def find_section_clip(section_index):
    """Find the exact chapter clip, preferring MP4 then WebM."""
    folder = clips_dir()
    for suffix in (".mp4", ".webm", ".mov"):
        candidate = folder / f"presenter_section_{int(section_index):02d}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def probe_duration(path, ffprobe="ffprobe"):
    """Read media duration; fail clearly if FFprobe cannot inspect the clip."""
    result = subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path)
    ], text=True).strip()
    try:
        duration = float(result)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Could not read Talking Avatar clip duration: {path}") from exc
    if duration <= 0:
        raise ValueError(f"Talking Avatar clip has invalid duration: {path}")
    return duration
