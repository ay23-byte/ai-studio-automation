"""Intel OpenVINO presenter adapter.

This adapter runs a separately installed, user-approved audio-driven presenter
backend on an OpenVINO device. The repository does not bundle face-animation
weights: model weights have separate licences and must be supplied by the user.

The external runner contract is:
  python RUNNER --audio AUDIO.wav --image PORTRAIT --output OUTPUT.mp4 --device GPU

The runner must produce the requested MP4 and return a non-zero exit code on
failure. This keeps model-specific preprocessing out of the main video pipeline.
"""
import os
import shutil
import subprocess
from pathlib import Path


def available_devices():
    """Return OpenVINO devices, or an empty list when OpenVINO is unavailable."""
    try:
        from openvino import Core
        return list(Core().available_devices)
    except Exception:
        return []


def generate_section_presenter(
    audio_path, section_start, duration, section_index, build_dir,
    ffmpeg="ffmpeg", ffprobe="ffprobe",
):
    """Render one audio-driven presenter segment via a configured OpenVINO runner."""
    if os.getenv("PRESENTER_ENABLED", "1").strip().lower() not in {"1", "true", "yes"}:
        return None

    devices = available_devices()
    requested = os.getenv("OPENVINO_DEVICE", "GPU").strip().upper() or "GPU"
    if requested not in devices:
        raise RuntimeError(
            f"OpenVINO device {requested!r} is unavailable. Detected devices: {devices or 'none'}. "
            "Install OpenVINO and the Intel graphics driver, or select PRESENTER_MODE=still_cpu."
        )

    runner = Path(os.getenv("OPENVINO_PRESENTER_SCRIPT", "")).expanduser()
    portrait = Path(os.getenv("PRESENTER_IMAGE", "")).expanduser()
    audio_path = Path(audio_path)
    if not runner.is_file():
        raise RuntimeError(
            "OPENVINO_PRESENTER_SCRIPT must point to an installed audio-driven presenter "
            "runner. OpenVINO runtime/device detection alone does not provide lip-sync model weights."
        )
    if not portrait.is_file():
        raise RuntimeError(f"Presenter portrait not found: {portrait}")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Narration audio not found: {audio_path}")

    duration = max(2.0, min(float(duration), 8.0))
    out_dir = Path(build_dir) / "presenter" / f"openvino_section_{section_index:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_segment = out_dir / "narration.wav"
    output = out_dir / "presenter.mp4"
    subprocess.run([
        ffmpeg, "-y", "-loglevel", "error", "-ss", f"{float(section_start):.3f}",
        "-t", f"{duration:.3f}", "-i", str(audio_path), "-vn", "-ac", "1",
        "-ar", "16000", str(audio_segment),
    ], check=True)
    python = os.getenv("OPENVINO_PYTHON", "python")
    subprocess.run([
        python, str(runner), "--audio", str(audio_segment), "--image", str(portrait),
        "--output", str(output), "--device", requested,
    ], check=True)
    if not output.is_file():
        raise RuntimeError("OpenVINO presenter runner completed without producing the requested MP4.")

    measured = float(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
        str(output),
    ]).strip())
    if abs(measured - duration) > 0.9:
        raise RuntimeError(
            f"OpenVINO presenter clip duration {measured:.2f}s does not match "
            f"requested {duration:.2f}s."
        )
    return output
