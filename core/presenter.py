"""Optional SadTalker adapter for an owned/licensed presenter portrait.

This adapter is opt-in. GitHub-hosted runners do not provide a supported
NVIDIA GPU; use a GPU workstation or self-hosted GPU Actions runner with
SadTalker and its model weights already installed.
"""
import os
import shutil
import subprocess
from pathlib import Path

def generate_section_presenter(audio_path, section_start, duration, section_index, build_dir, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Generate a short lip-synced chapter intro, or return None if disabled."""
    if os.getenv("PRESENTER_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    repo = Path(os.getenv("SADTALKER_DIR", "")).expanduser()
    portrait = Path(os.getenv("PRESENTER_IMAGE", "")).expanduser()
    inference = repo / "inference.py"
    if not inference.is_file() or not portrait.is_file():
        raise RuntimeError("AI presenter enabled but SadTalker inference.py or PRESENTER_IMAGE is missing.")
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Narration audio not found: {audio_path}")
    duration = max(2.0, min(float(duration), 8.0))
    out_dir = Path(build_dir) / "presenter" / f"section_{section_index:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_segment = out_dir / "narration_intro.wav"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{float(section_start):.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_segment)], check=True)
    result_dir = out_dir / "sadtalker_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([os.getenv("PYTHON", "python"), str(inference), "--driven_audio", str(audio_segment), "--source_image", str(portrait), "--result_dir", str(result_dir), "--preprocess", "crop", "--still"], cwd=str(repo), check=True)
    candidates = sorted(result_dir.rglob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("SadTalker completed but did not produce an MP4 presenter clip.")
    output = out_dir / "presenter.mp4"
    shutil.copy2(candidates[0], output)
    measured = float(subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(output)]).strip())
    if measured < 1.0 or measured > duration + 2.5:
        raise RuntimeError(f"Presenter clip duration {measured:.2f}s does not match requested {duration:.2f}s narration segment.")
    return output
