"""Optional SadTalker adapter for an owned/licensed presenter portrait.

This adapter is opt-in. GitHub-hosted runners do not provide a supported
NVIDIA GPU; use a GPU workstation or self-hosted GPU Actions runner with
SadTalker and its model weights already installed.
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from core.ffmpeg_utils import render_timeout, tool_timeout

def generate_section_presenter(audio_path, section_start, duration, section_index, build_dir, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Generate a short lip-synced chapter intro, or return None if disabled."""
    if os.getenv("PRESENTER_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        return None
    repo = Path(os.getenv("SADTALKER_DIR", "")).expanduser()
    portrait = Path(os.getenv("PRESENTER_IMAGE", "")).expanduser()
    inference = repo / "inference.py"
    if not inference.is_file() or not portrait.is_file():
        raise RuntimeError("AI presenter enabled but SadTalker inference.py or PRESENTER_IMAGE is missing.")
    required_checkpoints = [
        repo / "checkpoints" / "SadTalker_V0.0.2_256.safetensors",
        repo / "checkpoints" / "mapping_00229-model.pth.tar",
    ]
    missing = [str(item) for item in required_checkpoints if not item.is_file()]
    if missing:
        raise RuntimeError(
            "SadTalker pretrained checkpoints are missing: " + ", ".join(missing) +
            ". Run the official scripts/download_models.sh from the SadTalker directory."
        )
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Narration audio not found: {audio_path}")
    duration = max(2.0, min(float(duration), 8.0))
    out_dir = Path(build_dir) / "presenter" / f"section_{section_index:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_segment = out_dir / "narration_intro.wav"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{float(section_start):.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_segment)], check=True, timeout=render_timeout())
    result_dir = out_dir / "sadtalker_results"
    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    runner_python = os.getenv("SADTALKER_PYTHON") or os.getenv("PYTHON", "python")
    device = os.getenv("SADTALKER_DEVICE", "cpu").strip().lower()
    if device not in {"cpu", "cuda", "auto"}:
        raise ValueError("SADTALKER_DEVICE must be cpu, cuda, or auto.")
    command = [
        runner_python, str(inference),
        "--driven_audio", str(audio_segment),
        "--source_image", str(portrait),
        "--result_dir", str(result_dir),
        "--preprocess", "crop", "--still",
        "--size", "256", "--batch_size", "1",
    ]
    if device == "cpu":
        command.append("--cpu")
    subprocess.run(command, cwd=str(repo), check=True, timeout=tool_timeout())
    candidates = sorted(result_dir.rglob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("SadTalker completed but did not produce an MP4 presenter clip.")
    output = out_dir / "presenter.mp4"
    shutil.copy2(candidates[0], output)

    from core.presenter_quality import inspect_presenter_clip
    validation = inspect_presenter_clip(output, expected_duration=duration,
                                        tolerance_seconds=0.75, require_audio=True, ffprobe=ffprobe)
    if validation["status"] == "FAIL":
        raise RuntimeError("SadTalker presenter clip failed A/V validation: " +
                           "; ".join(validation["issues"]))
    digest = hashlib.sha256(audio_segment.read_bytes()).hexdigest()
    metadata = {
        "provider": "SadTalker", "presenter_mode": "sadtalker",
        "portrait_asset": portrait.name, "driven_audio_sha256": digest,
        "driven_audio_duration_seconds": duration, "validation": validation,
        "licence_status": "Confirm portrait permission and current SadTalker code/model-weight terms before publication.",
        "lip_sync_verified": False,
        "notice": "Audio-driven facial animation with technical A/V duration checks; not a guarantee of phoneme-perfect lip-sync.",
    }
    output.with_suffix(output.suffix + ".source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "presenter_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output
