"""FFmpeg/FFprobe invocation and lightweight frame analysis (free stack)."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import V2Config
from .errors import RenderError


def resolve_ffmpeg(config: V2Config) -> str | None:
    candidate = shutil.which(config.ffmpeg_binary)
    if candidate:
        return candidate
    candidate = shutil.which("ffmpeg")
    if candidate:
        return candidate
    # Optional local fallback for sandboxes. Never a production requirement.
    if importlib.util.find_spec("imageio_ffmpeg") is not None:
        import imageio_ffmpeg
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            return None
    return None


def resolve_ffprobe(config: V2Config) -> str | None:
    return shutil.which(config.ffprobe_binary) or shutil.which("ffprobe")


def run(command: list[str], *, timeout: float = 300.0, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"external tool failed: {str(exc)[:300]}") from exc
    if check and result.returncode != 0:
        raise RenderError(f"command failed ({result.returncode}): {' '.join(command[:6])} … "
                          + (result.stderr or "")[-800:])
    return result


def probe(path: Path, config: V2Config) -> dict:
    path = Path(path)
    ffprobe = resolve_ffprobe(config)
    if ffprobe:
        result = run([ffprobe, "-v", "error", "-show_entries",
                      "format=duration,size,start_time:stream=codec_type,codec_name,width,height,r_frame_rate",
                      "-of", "json", str(path)], timeout=60)
        try:
            return json.loads(result.stdout or "{}")
        except ValueError as exc:
            raise RenderError("ffprobe returned invalid JSON") from exc
    ffmpeg = resolve_ffmpeg(config)
    if not ffmpeg:
        raise RenderError("neither ffprobe nor ffmpeg is available for media validation")
    result = run([ffmpeg, "-hide_banner", "-i", str(path)], timeout=60, check=False)
    text = result.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    duration = None
    if duration_match:
        h, m, s = duration_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)
    video = re.search(r"Video:\s*(\w+).*?(\d{2,5})x(\d{2,5})", text)
    audio = re.search(r"Audio:\s*(\w+)", text)
    streams = []
    if video:
        streams.append({"codec_type": "video", "codec_name": video.group(1),
                        "width": int(video.group(2)), "height": int(video.group(3))})
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio.group(1)})
    return {"format": {"duration": str(duration or 0), "size": str(path.stat().st_size)},
            "streams": streams, "probe_fallback": True}


def extract_frames(video: Path, output_dir: Path, config: V2Config, *, interval: float = 5.0) -> list[Path]:
    ffmpeg = resolve_ffmpeg(config)
    if not ffmpeg:
        return []
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps=1/{max(0.5, interval)}", str(output_dir / "frame_%03d.png")],
        timeout=config.ffmpeg_timeout_seconds, check=False)
    return sorted(output_dir.glob("frame_*.png"))


def average_hash(path: Path) -> str:
    from PIL import Image
    with Image.open(path) as image:
        small = image.convert("L").resize((8, 8))
        pixels = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(small.getdata())
    avg = sum(pixels) / len(pixels)
    return "".join("1" if p > avg else "0" for p in pixels)


def hamming(left: str, right: str) -> int:
    return sum(a != b for a, b in zip(left, right))


def black_intervals(video: Path, config: V2Config) -> list[tuple[float, float]]:
    ffmpeg = resolve_ffmpeg(config)
    if not ffmpeg:
        return []
    # A low pixel threshold avoids mistaking intentionally dark, labelled
    # explainer backgrounds for an unintended black screen.
    result = run([ffmpeg, "-hide_banner", "-i", str(video), "-vf",
                  "blackdetect=d=0.8:pix_th=0.02", "-an", "-f", "null", "-"],
                 timeout=config.ffmpeg_timeout_seconds, check=False)
    intervals = []
    for match in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", result.stderr):
        intervals.append((float(match.group(1)), float(match.group(2))))
    return intervals
