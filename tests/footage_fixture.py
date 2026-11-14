"""Small real media files for footage tests (made with FFmpeg, never downloaded)."""
import subprocess
from pathlib import Path

from core.ffmpeg_utils import ffmpeg_bin


def have_ffmpeg() -> bool:
    """True when FFmpeg and FFprobe can be run; tests that build real clips skip without them."""
    import shutil
    try:
        from core.ffmpeg_utils import ffmpeg_bin, ffprobe_bin
        return bool(shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin()))
    except Exception:
        return False


def make_clip(path, *, seconds=4, size="640x360", pattern="testsrc2", rate=24, audio=False, tag="") -> Path:
    """Write a short, valid H.264 clip. ``tag`` is stored in the file, so each tag gives different bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg_bin(), "-y", "-loglevel", "error",
               "-f", "lavfi", "-i", f"{pattern}=size={size}:rate={rate}"]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440"]
    command += ["-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "1500k",
                "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-shortest"]
    if tag:
        command += ["-metadata", f"comment={tag}"]
    command.append(str(path))
    subprocess.run(command, check=True, timeout=120)
    return path


def truncated_copy(source, target, keep=0.25) -> Path:
    """A copy with a readable header and missing picture data (an interrupted transfer)."""
    data = Path(source).read_bytes()
    fast = Path(target).with_name("faststart_" + Path(target).name)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(source), "-c", "copy",
                    "-movflags", "+faststart", str(fast)], check=True, timeout=120)
    data = fast.read_bytes()
    fast.unlink()
    Path(target).write_bytes(data[: max(60_000, int(len(data) * keep))])
    return Path(target)
