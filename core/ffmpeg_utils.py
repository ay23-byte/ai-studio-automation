"""Shared FFmpeg/FFprobe helpers: safe filter paths, capability probing, probes.

Root causes addressed here:

* Subtitle paths were interpolated raw into ``-filter_complex`` strings. On
  Windows (``F:\\GitHub\\...\\subs.ass``) the drive colon and backslashes are
  filter-graph syntax, so every render failed. ``filter_path`` escapes them.
* Editors hard-required the ``xfade`` filter (FFmpeg >= 4.3). Older builds now
  fall back to hard cuts through ``concat`` instead of crashing.
* libass silently substituted fonts without Devanagari coverage. A fonts
  directory can now be passed explicitly (``SUBTITLE_FONTS_DIR``).
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path


class FFmpegUnavailable(RuntimeError):
    """Raised when ffmpeg/ffprobe are required but not installed."""


class FFmpegTimeout(RuntimeError):
    """A media command exceeded its time limit and was stopped."""


# A probe reads headers only; two minutes means the file or the disk is broken.
PROBE_TIMEOUT = 120


def _seconds(name: str, default: int):
    """Time limit from the environment; ``0`` or a negative value disables it."""
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = float(default)
    return value if value > 0 else None


def render_timeout():
    """Limit for one FFmpeg render or mix (``FFMPEG_TIMEOUT_SECONDS``, default 1 hour).

    Without a limit a stalled encoder runs until the workflow itself is killed,
    which loses the checkpoint, the run report and every artifact.
    """
    return _seconds("FFMPEG_TIMEOUT_SECONDS", 3600)


def tool_timeout():
    """Limit for an external generator such as the avatar or SadTalker runner
    (``EXTERNAL_TOOL_TIMEOUT_SECONDS``, default 2 hours)."""
    return _seconds("EXTERNAL_TOOL_TIMEOUT_SECONDS", 7200)


def ffmpeg_bin() -> str:
    return os.getenv("FFMPEG_BINARY", "ffmpeg")


def ffprobe_bin() -> str:
    return os.getenv("FFPROBE_BINARY", "ffprobe")


def tools_available() -> dict:
    """Report presence of ffmpeg/ffprobe without raising."""
    return {
        "ffmpeg": shutil.which(ffmpeg_bin()) is not None,
        "ffprobe": shutil.which(ffprobe_bin()) is not None,
    }


def require_tools() -> None:
    missing = [name for name, present in tools_available().items() if not present]
    if missing:
        raise FFmpegUnavailable(
            "Required tool(s) not found on PATH: " + ", ".join(missing)
            + ". Install FFmpeg (https://ffmpeg.org/download.html) and reopen the terminal."
        )


def filter_path(path) -> str:
    """Escape a filesystem path for use as a filter option value.

    Works for POSIX and Windows paths. The result is safe inside
    ``-vf``/``-filter_complex`` (both option-level and graph-level escaping).
    """
    text = str(Path(path).resolve()) if not isinstance(path, str) else path
    text = text.replace("\\", "/")
    # Inside single quotes the graph parser keeps everything literal, so only
    # the option-level separator ':' needs a backslash.
    text = text.replace(":", "\\:")
    # An apostrophe cannot be escaped inside a quoted section: close the quote,
    # emit an option-level-escaped apostrophe, and reopen the quote.
    text = text.replace("'", "'\\\\\\''")
    return "'" + text + "'"


def subtitles_filter(ass_path, fonts_dir=None) -> str:
    """Return a ``subtitles=`` filter with an escaped path and optional fonts dir."""
    value = "subtitles=" + filter_path(ass_path)
    fonts_dir = fonts_dir if fonts_dir is not None else os.getenv("SUBTITLE_FONTS_DIR", "").strip()
    if not fonts_dir:
        from core.fonts import bundled_or_user_fonts_dir
        fonts_dir = bundled_or_user_fonts_dir()
    if fonts_dir and Path(fonts_dir).is_dir():
        value += ":fontsdir=" + filter_path(fonts_dir)
    return value


@functools.lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    """True when the installed FFmpeg build provides the named filter."""
    try:
        result = subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def xfade_supported() -> bool:
    if os.getenv("FFMPEG_DISABLE_XFADE", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return has_filter("xfade")


def probe_duration(path) -> float:
    """Return container duration in seconds (0.0 if it cannot be probed)."""
    try:
        out = subprocess.check_output(
            [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            stderr=subprocess.DEVNULL, timeout=60,
        )
        return max(0.0, float(out.strip()))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def probe_streams(path) -> dict:
    """Return a compact, JSON-safe description of the first video/audio streams."""
    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = None
    if video:
        try:
            num, den = str(video.get("avg_frame_rate") or "0/1").split("/")
            fps = round(float(num) / float(den), 3) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration_seconds": round(duration, 3),
        "size_bytes": Path(path).stat().st_size,
        "has_video": bool(video), "has_audio": bool(audio),
        "width": int(video.get("width") or 0) if video else 0,
        "height": int(video.get("height") or 0) if video else 0,
        "video_codec": video.get("codec_name") if video else None,
        "pix_fmt": video.get("pix_fmt") if video else None,
        "fps": fps,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": int(audio.get("sample_rate") or 0) if audio else 0,
        "audio_channels": int(audio.get("channels") or 0) if audio else 0,
    }


class FFmpegError(subprocess.CalledProcessError):
    """CalledProcessError whose message includes the tail of FFmpeg's stderr."""

    def __str__(self):
        program = Path(str(self.cmd[0])).name if self.cmd else "ffmpeg"
        detail = (self.stderr or "").strip()
        return f"{program} exited with status {self.returncode}" + (f":\n{detail}" if detail else ".")


def run(cmd, timeout=None) -> None:
    """Run an FFmpeg command, surfacing stderr in the exception on failure.

    ``timeout=None`` applies :func:`render_timeout`; the process is killed when
    the limit is reached, so a stalled render fails instead of hanging the job.
    """
    cmd = [str(part) for part in cmd]
    limit = render_timeout() if timeout is None else (timeout if timeout > 0 else None)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=limit,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise FFmpegUnavailable(
            f"{cmd[0]} was not found on PATH. Install FFmpeg and reopen the terminal."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        tail = str(exc.stderr or "").strip().splitlines()[-6:] if isinstance(exc.stderr, str) else []
        raise FFmpegTimeout(
            f"{Path(cmd[0]).name} was stopped after {int(limit)} seconds without finishing "
            f"(output: {cmd[-1]}). Raise FFMPEG_TIMEOUT_SECONDS if this render is expected to take longer."
            + ("\n" + "\n".join(tail) if tail else "")
        ) from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-12:]
        raise FFmpegError(result.returncode, cmd, output=result.stdout, stderr="\n".join(tail))


def trimmed_concat_chain(durations, out_label: str = "vcat", fps: int = 30) -> str:
    """Hard-cut chain that trims each input to its on-screen duration.

    Segments are rendered with a transition overlap; without ``xfade`` that
    overlap must be removed or picture and narration drift apart.
    """
    parts, labels = [], []
    for index, duration in enumerate(durations):
        label = f"[t{index}]"
        parts.append(f"[{index}:v]trim=duration={max(float(duration), 0.04):.3f},"
                     f"setpts=PTS-STARTPTS,fps={fps},setsar=1{label}")
        labels.append(label)
    parts.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[{out_label}]")
    return ";".join(parts)


def concat_chain(input_count: int, out_label: str = "vcat") -> str:
    """Hard-cut concat filter chain for builds without ``xfade``."""
    inputs = "".join(f"[{index}:v]" for index in range(input_count))
    return f"{inputs}concat=n={input_count}:v=1:a=0[{out_label}]"
