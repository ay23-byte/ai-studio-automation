"""Optional local LTX-Video generation through OpenVINO GenAI.

No hosted endpoint, API key, subscription, or paid inference provider is used.
The model weights must be exported locally by the operator first.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def generate_ltx_video(
    prompt: str,
    output_path: str | Path,
    *,
    aspect_ratio: str = "9:16",
    model_dir: str | Path | None = None,
) -> bool:
    """Generate one short clip with a locally exported OpenVINO LTX-Video model."""
    target = Path(output_path)
    model_path = Path(model_dir or os.getenv("OPENVINO_LTX_MODEL_DIR", "")).expanduser()
    if not prompt or not str(prompt).strip():
        print("OpenVINO LTX skipped: empty prompt.")
        return False
    if not str(model_path) or not model_path.is_dir():
        print("OpenVINO LTX skipped: set OPENVINO_LTX_MODEL_DIR to an exported model folder.")
        return False

    try:
        import numpy as np
        import openvino as ov
        import openvino_genai
    except ImportError as exc:
        print(f"OpenVINO LTX skipped: install openvino-genai and numpy ({exc}).")
        return False

    available = set(ov.Core().available_devices)
    requested_device = os.getenv("OPENVINO_DEVICE", "GPU").strip().upper()
    if requested_device == "AUTO":
        device = "AUTO"
    elif requested_device in available:
        device = requested_device
    elif requested_device == "GPU" and os.getenv("OPENVINO_ALLOW_CPU_FALLBACK", "false").lower() == "true" and "CPU" in available:
        device = "CPU"
        print("OpenVINO GPU unavailable; explicitly enabled CPU fallback selected.")
    else:
        print(f"OpenVINO LTX skipped: requested device {requested_device} unavailable; found {sorted(available)}.")
        return False

    if aspect_ratio == "9:16":
        width, height = 480, 832
    else:
        width, height = 704, 384
    width = _int_env("OPENVINO_VIDEO_WIDTH", width, 320, 1280)
    height = _int_env("OPENVINO_VIDEO_HEIGHT", height, 320, 1280)
    frames = _int_env("OPENVINO_VIDEO_FRAMES", 57, 9, 97)
    # LTX works best with frame counts of 8n+1 and dimensions divisible by 32.
    frames = max(9, min(97, ((frames - 1) // 8) * 8 + 1))
    width = max(320, (width // 32) * 32)
    height = max(320, (height // 32) * 32)
    steps = _int_env("OPENVINO_VIDEO_STEPS", 25, 2, 50)
    fps = _int_env("OPENVINO_VIDEO_FPS", 25, 12, 30)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        print(f"Loading local LTX-Video model on OpenVINO {device}: {model_path}")
        pipe = openvino_genai.Text2VideoPipeline(str(model_path), device)

        def progress(step, total, _latent):
            print(f"OpenVINO LTX generation step {step + 1}/{total}")
            return False

        result = pipe.generate(
            str(prompt),
            negative_prompt="worst quality, inconsistent motion, blurry, jittery, distorted, flicker, deformed objects",
            height=height,
            width=width,
            num_frames=frames,
            num_inference_steps=steps,
            num_videos_per_prompt=1,
            callback=progress,
            frame_rate=fps,
            guidance_scale=3.0,
        )
        video = np.asarray(result.video.data[0], dtype=np.uint8)
        if video.ndim != 4 or video.shape[0] < 9 or video.shape[-1] != 3:
            raise RuntimeError(f"Unexpected generated video tensor shape: {video.shape}")
        actual_height, actual_width = video.shape[1:3]
        command = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{actual_width}x{actual_height}", "-r", str(fps),
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast",
            "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(target),
        ]
        subprocess.run(command, input=video.tobytes(), check=True, capture_output=True)
        if not target.is_file() or target.stat().st_size < 50_000:
            raise RuntimeError("OpenVINO LTX output failed the minimum MP4 size check.")
        metadata = {
            "provider": "Local OpenVINO GenAI",
            "model": "Lightricks/LTX-Video (OpenVINO export)",
            "asset_type": "ai_generated_video",
            "device": device,
            "width": int(actual_width),
            "height": int(actual_height),
            "frames": int(video.shape[0]),
            "fps": fps,
            "prompt": str(prompt),
            "ai_disclosure": True,
            "generation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model_license_review": "Review the upstream LTX-Video model license and current terms before publication.",
            "human_visual_inspection_required": True,
            "notice": "Synthetic AI-generated footage. Never label or present it as authentic archival evidence.",
        }
        target.with_suffix(target.suffix + ".source.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"OpenVINO LTX generated: {target} ({actual_width}x{actual_height}, {video.shape[0]} frames)")
        return True
    except Exception as exc:
        target.unlink(missing_ok=True)
        print(f"OpenVINO LTX generation failed: {type(exc).__name__}: {exc}")
        return False
