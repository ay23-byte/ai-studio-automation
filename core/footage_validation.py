"""Inspect a downloaded footage file and say exactly why it is or is not usable.

The earlier checks returned a bare ``False``, so a build log could not tell an
HTML error page from a truncated transfer or a 240-pixel-high clip. Every
rejection here carries a reason code and the measured values.

A file passes only when ffprobe can read a real video stream from it and FFmpeg
can decode the opening seconds.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin

# Containers the renderer is known to read. ffprobe reports a comma-separated
# family name such as "mov,mp4,m4a,3gp,3g2,mj2" or "matroska,webm".
SUPPORTED_CONTAINERS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "ogg", "mpegts", "avi", "mpeg"}
SUPPORTED_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".ogv", ".mkv", ".mpg", ".mpeg")

# Hard floors. Anything smaller is not footage a 1080p documentary can show.
# 320x240 is the size of the Library of Congress paper-print transfers, which are
# genuine archival film; they pass the floor and lose ranking points instead.
MIN_BYTES = 50_000
MIN_DURATION = 2.0
MIN_WIDTH = 320
MIN_HEIGHT = 240

_TEXT_SIGNATURES = (b"<!doctype", b"<html", b"<?xml", b"<head", b"<body", b"{\"", b"{\n", b"[{", b"#extm3u",
                    b"access denied", b"<error", b"not found")


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return float(default)


def sha256_of(path: Path, limit_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    read = 0
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
            if limit_bytes and read >= limit_bytes:
                break
    return digest.hexdigest()


def looks_like_text(path: Path) -> str:
    """Return a description when the file starts like HTML/JSON/a playlist, else ''."""
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(512)
    except OSError:
        return ""
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    for signature in _TEXT_SIGNATURES:
        if stripped.startswith(signature):
            return "file begins with " + signature.decode("ascii", "replace").strip() + " (a web page or API reply, not media)"
    return ""


def _fraction(value) -> float:
    try:
        text = str(value or "0")
        if "/" in text:
            top, bottom = text.split("/", 1)
            return float(top) / float(bottom) if float(bottom) else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def inspect_media(path) -> dict:
    """ffprobe the file. Returns measured facts, or ``{"error": ...}``."""
    path = Path(path)
    try:
        done = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-show_entries",
             "format=format_name,duration,size,bit_rate:"
             "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,pix_fmt",
             "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"ffprobe did not finish within {PROBE_TIMEOUT} seconds"}
    except OSError as exc:
        return {"error": f"ffprobe is not available: {exc}"}
    if done.returncode != 0:
        detail = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return {"error": "ffprobe could not read the file: " + (detail[-1][-200:] if detail else "no detail")}
    raw = done.stdout
    try:
        info = json.loads(raw)
    except ValueError:
        return {"error": "ffprobe returned unreadable output"}
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _fraction(fmt.get("duration")) or _fraction((video or {}).get("duration"))
    return {
        "container": str(fmt.get("format_name") or ""),
        "duration_seconds": round(duration, 3),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "bit_rate": int(_fraction(fmt.get("bit_rate"))),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "video_codec": str((video or {}).get("codec_name") or ""),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": round(_fraction((video or {}).get("avg_frame_rate")) or _fraction((video or {}).get("r_frame_rate")), 3),
        "pixel_format": str((video or {}).get("pix_fmt") or ""),
    }


def _frames_in(path, interval: str) -> tuple[int, str]:
    """Decode one ffprobe read interval; returns (frames decoded, problem)."""
    try:
        done = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-read_intervals", interval, "-show_entries", "stream=nb_read_frames",
             "-of", "json", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 0, f"decoding did not finish within {PROBE_TIMEOUT} seconds"
    except OSError as exc:
        return 0, f"ffprobe is not available: {exc}"
    try:
        stream = (json.loads(done.stdout or b"{}").get("streams") or [{}])[0]
        return int(stream.get("nb_read_frames") or 0), ""
    except (ValueError, TypeError, IndexError):
        return 0, ""


def decode_check(path, duration: float = 0.0, seconds: float = 2.0) -> str:
    """Decode the opening and the closing seconds. Returns '' when frames decode, else the reason.

    A truncated transfer, or a file with a valid header and a garbage payload,
    still has a readable stream description. Counting decoded frames at both ends
    catches it. Old archive transfers often log harmless decoder warnings, so the
    test is "frames came out", not "no message was printed".
    """
    frames, problem = _frames_in(path, f"%+{seconds:g}")
    if problem:
        return problem
    if frames < 2:
        return f"only {frames} frame(s) decoded from the first {seconds:g} seconds"
    if duration and duration > seconds * 2:
        # Strict at the tail only: that is where a short transfer shows.
        tail_start = max(0.0, duration - 1.5)
        try:
            done = subprocess.run(
                [ffmpeg_bin(), "-v", "error", "-xerror", "-ss", f"{tail_start:.3f}", "-i", str(path),
                 "-map", "0:v:0", "-frames:v", "3", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return f"decoding the last seconds did not finish within {PROBE_TIMEOUT} seconds"
        except OSError as exc:
            return f"FFmpeg is not available: {exc}"
        if done.returncode != 0:
            lines = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            detail = lines[-1][-160:] if lines else f"exit code {done.returncode}"
            return ("the last 1.5 seconds do not decode; the file is shorter than its header says "
                    f"(truncated or damaged): {detail}")
    return ""


def validate_media(path, *, min_width: int | None = None, min_height: int | None = None,
                   min_duration: float | None = None, decode: bool = True, with_hash: bool = True) -> dict:
    """Validate a downloaded file. Result: ``{"ok", "status", "reason", ...measured facts}``.

    ``status`` is one of ``valid``, ``missing``, ``empty``, ``too_small``,
    ``not_media``, ``corrupt``, ``unsupported_format``, ``no_video_stream``,
    ``too_short``, ``low_resolution``, ``undecodable``.
    """
    path = Path(path)
    min_width = int(min_width if min_width is not None else _env_number("FOOTAGE_MIN_WIDTH", MIN_WIDTH))
    min_height = int(min_height if min_height is not None else _env_number("FOOTAGE_MIN_HEIGHT", MIN_HEIGHT))
    min_duration = float(min_duration if min_duration is not None
                         else _env_number("FOOTAGE_MIN_DURATION_SECONDS", MIN_DURATION))

    def fail(status, reason, **facts):
        return {"ok": False, "status": status, "reason": reason, **facts}

    if not path.is_file():
        return fail("missing", "no file was written")
    size = path.stat().st_size
    if size == 0:
        return fail("empty", "the file is empty", size_bytes=0)
    text = looks_like_text(path)
    if text:
        return fail("not_media", text, size_bytes=size)
    if size < MIN_BYTES:
        return fail("too_small", f"the file is {size} bytes; real footage is at least {MIN_BYTES} bytes",
                    size_bytes=size)
    facts = inspect_media(path)
    if facts.get("error"):
        return fail("corrupt", facts["error"], size_bytes=size)
    families = {name.strip() for name in facts["container"].split(",") if name.strip()}
    if not families & SUPPORTED_CONTAINERS:
        return fail("unsupported_format", f"container {facts['container'] or 'unknown'} is not supported", **facts)
    if not facts["has_video"]:
        return fail("no_video_stream", "the file has no video stream", **facts)
    if facts["duration_seconds"] < min_duration:
        return fail("too_short", f"duration {facts['duration_seconds']:.1f}s is below {min_duration:g}s", **facts)
    if facts["width"] < min_width or facts["height"] < min_height:
        return fail("low_resolution",
                    f"{facts['width']}x{facts['height']} is below the {min_width}x{min_height} floor", **facts)
    if decode:
        problem = decode_check(path, facts["duration_seconds"])
        if problem:
            return fail("undecodable", problem, **facts)
    result = {"ok": True, "status": "valid", "reason": "ffprobe read a video stream and the opening frames decode",
              **facts}
    if with_hash:
        result["sha256"] = sha256_of(path)
    return result


def technical_quality(width: int, height: int, duration: float = 0.0, wanted_seconds: float = 0.0) -> float:
    """0..1 score from measured (or provider-declared) picture size and length."""
    pixels = max(0, int(width or 0)) * max(0, int(height or 0))
    if pixels >= 1920 * 1080:
        size_score = 1.0
    elif pixels >= 1280 * 720:
        size_score = 0.8
    elif pixels >= 854 * 480:
        size_score = 0.55
    elif pixels >= 640 * 360:
        size_score = 0.4
    elif pixels >= MIN_WIDTH * MIN_HEIGHT:
        size_score = 0.2
    else:
        size_score = 0.0 if pixels else 0.3  # unknown size is neither rewarded nor treated as tiny
    if not duration:
        length_score = 0.6
    elif wanted_seconds and duration + 0.25 < wanted_seconds:
        length_score = max(0.2, duration / wanted_seconds)
    else:
        length_score = 1.0
    return round(0.8 * size_score + 0.2 * length_score, 4)
