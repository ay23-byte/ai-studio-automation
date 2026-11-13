"""CPU-only documentary presenter scene renderer.

This creates a silent 16:9 Ken-Burns-style scene from an owned/licensed still
portrait. The main editor keeps the continuous narration audio track, so this
is deliberately NOT represented as lip-sync.
"""
import os
import shutil
import subprocess
from pathlib import Path
from core.ffmpeg_utils import PROBE_TIMEOUT, render_timeout

W, H = 1920, 1080


def _enabled():
    return os.getenv("PRESENTER_ENABLED", "1").strip().lower() in {"1", "true", "yes"}


def render_presenter_scene(section_index, duration, build_dir, image_path=None,
                           ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Return a silent MP4 presenter scene, or None when disabled/unavailable."""
    if not _enabled():
        return None

    configured = image_path or os.getenv(
        "PRESENTER_IMAGE", "assets/presenter/presenter_master.png"
    )
    portrait = Path(configured).expanduser()
    if not portrait.is_file():
        print(
            f"CPU presenter skipped: portrait not found at {portrait}. "
            "Add your approved presenter image to assets/presenter/presenter_master.png."
        )
        return None

    try:
        from PIL import Image
        with Image.open(portrait) as im:
            im.verify()
        with Image.open(portrait) as im:
            if im.width < 512 or im.height < 512:
                raise ValueError("Presenter portrait must be at least 512x512 pixels.")
    except Exception as exc:
        raise ValueError(f"Invalid presenter portrait {portrait}: {exc}") from exc

    duration = float(duration)
    if duration < 1.0:
        raise ValueError("Presenter scene duration must be at least 1 second.")
    out_dir = Path(build_dir) / "presenter"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"presenter_section_{int(section_index):02d}.mp4"

    # Fit portrait into a cinematic 16:9 frame with a dark blurred-fill background.
    # A gentle zoom keeps the still image from feeling completely static.
    frames = max(int(round(duration * 30)), 30)
    zoom = "min(1.0+0.00035*on,1.035)"
    vf = (
        "[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={W}:{H},boxblur=24:2[bg];"
        f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x101318[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={W}x{H}:fps=30,format=yuv420p"
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", "30", "-i", str(portrait),
        "-filter_complex", vf, "-t", f"{duration:.3f}",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30", "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True, timeout=render_timeout())
    measured = float(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(output)
    ], timeout=PROBE_TIMEOUT).strip())
    if abs(measured - duration) > 0.75:
        raise RuntimeError(
            f"Presenter scene duration {measured:.2f}s differs from requested {duration:.2f}s."
        )
    return output
