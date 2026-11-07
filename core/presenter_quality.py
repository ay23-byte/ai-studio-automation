"""Technical validation for audio-driven presenter clips.

This verifies stream presence, dimensions, frame rate and A/V duration alignment.
It does not prove phoneme-level mouth synchronisation or human likeness quality.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _duration(stream: dict[str, Any], fallback: float) -> float:
    try:
        return float(stream.get("duration") or fallback)
    except (TypeError, ValueError):
        return fallback


def _fps(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / max(float(denominator), 1e-9)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def inspect_presenter_clip(path: str | Path, expected_duration: float | None = None,
                           tolerance_seconds: float = 0.75, require_audio: bool = True,
                           ffprobe: str = "ffprobe") -> dict[str, Any]:
    """Check the presenter file's media streams and duration alignment."""
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"Presenter clip not found: {media}")
    probe = subprocess.check_output([
        ffprobe, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(media),
    ], text=True)
    payload = json.loads(probe or "{}")
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    format_duration = float((payload.get("format") or {}).get("duration") or 0)
    issues, warnings = [], []
    if video is None:
        issues.append("Presenter clip has no video stream.")
    if require_audio and audio is None:
        issues.append("Audio-driven presenter clip has no embedded audio stream.")
    video_duration = _duration(video, format_duration) if video else 0.0
    audio_duration = _duration(audio, format_duration) if audio else 0.0
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    frame_rate = _fps((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate"))
    if video and (width < 256 or height < 256):
        issues.append(f"Presenter resolution is too low ({width}×{height}); minimum is 256×256.")
    if video and frame_rate is not None and frame_rate < 15:
        warnings.append(f"Presenter frame rate is low ({frame_rate:.2f} fps).")
    if video_duration <= 0:
        issues.append("Presenter video duration is unavailable.")
    if audio and audio_duration <= 0:
        issues.append("Presenter audio duration is unavailable.")
    av_delta = abs(video_duration - audio_duration) if audio else None
    if av_delta is not None and av_delta > tolerance_seconds:
        issues.append(
            f"Presenter audio/video durations differ by {av_delta:.2f}s (tolerance {tolerance_seconds:.2f}s)."
        )
    expected_delta = abs(video_duration - float(expected_duration)) if expected_duration is not None else None
    if expected_delta is not None and expected_delta > tolerance_seconds:
        issues.append(
            f"Presenter duration differs from its narration segment by {expected_delta:.2f}s."
        )
    return {
        "status": "FAIL" if issues else ("REVIEW" if warnings else "PASS"),
        "video_duration_seconds": round(video_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3) if audio else None,
        "av_duration_delta_seconds": round(av_delta, 3) if av_delta is not None else None,
        "expected_duration_seconds": expected_duration,
        "expected_duration_delta_seconds": round(expected_delta, 3) if expected_delta is not None else None,
        "width": width, "height": height, "frame_rate": round(frame_rate, 3) if frame_rate is not None else None,
        "has_video": video is not None, "has_audio": audio is not None,
        "issues": issues, "warnings": warnings,
        "lip_sync_verified": False,
        "notice": "Stream and duration alignment do not prove phoneme-level lip-sync. Human visual/audio review remains required.",
    }
