"""Technical validation for audio-driven presenter clips.

`inspect_presenter_clip` verifies stream presence, dimensions, frame rate and A/V duration
alignment. `analyse_presenter_motion` decodes the picture and measures what a viewer would
see: how many frames are really different, whether the picture freezes, and whether the
mouth region moves while the narration is audible.

Neither proves phoneme-level mouth synchronisation or human likeness; a person still has to
watch the clip, and `human_visual_review` stays "pending" until they do.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from core.ffmpeg_utils import PROBE_TIMEOUT


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
    ], text=True, timeout=PROBE_TIMEOUT)
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
    if video and frame_rate is not None and frame_rate < 5:
        issues.append(
            f"Presenter frame rate is unusably low ({frame_rate:.2f} fps); "
            "a talking presenter must contain enough real frames for reviewable motion."
        )
    elif video and frame_rate is not None and frame_rate < 15:
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


# A frame is counted as a repeat of the previous one when fewer than REPEAT_PIXELS of the
# 57,600 pixels in a 320x180 greyscale copy changed by REPEAT_LEVEL or more (0-255 scale).
# Re-encoding an unchanged picture shifts many pixels by one or two levels but none by four;
# the slowest idle sway of the avatar moves edges by far more. Measured on real renders:
# repeated frames 0 pixels, the quietest genuine frame 17.
REPEAT_LEVEL = 4
REPEAT_PIXELS = 6
MIN_DISTINCT_FPS = 15.0
MAX_FROZEN_SECONDS = 0.5
MOUTH_MOTION_DELTA = 0.25
MIN_VOICED_MOUTH_RATIO = 0.5
_ANALYSIS_SIZE = (320, 180)


def _longest_run(flags) -> int:
    longest = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def analyse_presenter_motion(path: str | Path, mouth_box: dict[str, float] | None = None,
                             mouth_track: list[float] | None = None,
                             ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                             min_distinct_fps: float = MIN_DISTINCT_FPS) -> dict[str, Any]:
    """Measure real picture motion in a presenter clip.

    A file can report 30 fps and still show a frozen face, when the same picture is repeated
    to fill the time. So the frame rate that counts here is the number of *different* frames
    per second. `mouth_box` is the normalised rectangle {x, y, width, height} that holds the
    mouth; without it the mouth checks are reported as not measured rather than guessed.
    `mouth_track` is the renderer's own record of the mouth shape on each frame.
    """
    import numpy as np

    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"Presenter clip not found: {media}")
    probe = subprocess.run([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames,avg_frame_rate,r_frame_rate:frame=best_effort_timestamp_time",
        "-of", "json", str(media),
    ], capture_output=True, text=True, timeout=PROBE_TIMEOUT * 4)
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {media.name}: {probe.stderr.strip()[-300:]}")
    payload = json.loads(probe.stdout or "{}")
    stream = (payload.get("streams") or [{}])[0]
    stamps = []
    for frame in payload.get("frames") or []:
        try:
            stamps.append(float(frame.get("best_effort_timestamp_time")))
        except (TypeError, ValueError):
            continue
    nominal_fps = _fps(stream.get("avg_frame_rate")) or _fps(stream.get("r_frame_rate")) or 0.0
    stored_frames = int(stream.get("nb_read_frames") or len(stamps) or 0)

    width, height = _ANALYSIS_SIZE
    # Frames are sampled at the file's nominal rate, so a gap in the timestamps shows up
    # here as repeated pictures, exactly as it would on screen.
    picture = subprocess.run([
        ffmpeg, "-v", "error", "-i", str(media), "-an", "-vf", f"scale={width}:{height},format=gray",
        "-f", "rawvideo", "-",
    ], capture_output=True, timeout=PROBE_TIMEOUT * 8)
    if picture.returncode != 0:
        raise RuntimeError(f"FFmpeg could not decode {media.name}: {picture.stderr.decode('utf-8', 'replace')[-300:]}")
    count = len(picture.stdout) // (width * height)
    issues: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "frames_stored": stored_frames, "frames_analysed": count,
        "nominal_frame_rate": round(nominal_fps, 3) if nominal_fps else None,
        "human_visual_review": "pending",
    }
    if count < 2 or nominal_fps <= 0:
        issues.append("Presenter clip has too few decodable frames to measure motion.")
        result.update({"status": "FAIL", "issues": issues, "warnings": warnings})
        return result
    frames = np.frombuffer(picture.stdout[:count * width * height], dtype=np.uint8).reshape(count, height, width).astype(np.int16)
    change = np.abs(frames[1:] - frames[:-1])
    delta = change.reshape(count - 1, -1).mean(axis=1)
    frozen = (change >= REPEAT_LEVEL).reshape(count - 1, -1).sum(axis=1) < REPEAT_PIXELS
    seconds = count / nominal_fps
    distinct = int(count - frozen.sum())
    distinct_fps = distinct / seconds
    longest_frozen = _longest_run(frozen) / nominal_fps
    gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    result.update({
        "duration_seconds": round(seconds, 3),
        "distinct_frames": distinct,
        "distinct_frame_rate": round(distinct_fps, 3),
        "repeated_frames": int(frozen.sum()),
        "repeated_frame_ratio": round(float(frozen.mean()), 4),
        "longest_frozen_seconds": round(longest_frozen, 3),
        "mean_frame_change": round(float(delta.mean()), 4),
        "timestamps_monotonic": all(gap > 0 for gap in gaps),
        "longest_timestamp_gap_seconds": round(max(gaps), 4) if gaps else None,
    })
    if gaps and not result["timestamps_monotonic"]:
        issues.append("Presenter video timestamps go backwards or repeat.")
    if gaps and max(gaps) > 2.5 / nominal_fps:
        warnings.append(f"Presenter video has a {max(gaps):.2f}s gap between frames.")
    if distinct_fps < min_distinct_fps:
        issues.append(
            f"Presenter clip shows only {distinct} different frames in {seconds:.1f}s "
            f"({distinct_fps:.1f} per second; minimum {min_distinct_fps:.0f}). "
            f"The file's stated {nominal_fps:.0f} fps is made of repeated pictures."
        )
    if longest_frozen > MAX_FROZEN_SECONDS:
        issues.append(f"Presenter picture is frozen for {longest_frozen:.2f}s (limit {MAX_FROZEN_SECONDS:.1f}s).")

    result["mouth_measured"] = False
    if mouth_box:
        try:
            left = int(max(0.0, float(mouth_box["x"])) * width)
            top = int(max(0.0, float(mouth_box["y"])) * height)
            right = int(min(1.0, float(mouth_box["x"]) + float(mouth_box["width"])) * width)
            bottom = int(min(1.0, float(mouth_box["y"]) + float(mouth_box["height"])) * height)
        except (KeyError, TypeError, ValueError):
            left = top = right = bottom = 0
        if right - left >= 4 and bottom - top >= 4:
            mouth = change[:, top:bottom, left:right].reshape(count - 1, -1).mean(axis=1)
            # When the head turns, every part of the face changes, the mouth included. The
            # strip of face just above the mouth moves with the head but not with speech, so
            # its change is subtracted: what is left is movement of the mouth itself.
            above = max(0, top - (bottom - top))
            if top - above >= 4:
                mouth = mouth - change[:, above:top, left:right].reshape(count - 1, -1).mean(axis=1)
            sound = subprocess.run([
                ffmpeg, "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
            ], capture_output=True, timeout=PROBE_TIMEOUT * 8)
            samples = np.frombuffer(sound.stdout[:len(sound.stdout) // 2 * 2], dtype=np.int16).astype(np.float64)
            per_frame = 16000 / nominal_fps
            loudness = np.array([
                float(np.sqrt(np.mean(samples[int(i * per_frame):int((i + 1) * per_frame)] ** 2)))
                if int((i + 1) * per_frame) <= len(samples) and per_frame >= 1 else 0.0
                for i in range(1, count)
            ])
            peak = float(loudness.max()) if len(loudness) else 0.0
            result["mouth_measured"] = True
            result["mouth_mean_change"] = round(float(mouth.mean()), 4)
            if peak < 200:
                warnings.append("Presenter clip narration is silent or almost silent; mouth timing could not be compared with it.")
                result["voiced_seconds"] = 0.0
            else:
                voiced = loudness > peak * 0.12
                quiet = loudness < peak * 0.03
                # A mouth holds still inside a long vowel, so each voiced frame is credited
                # with the largest mouth change within two frames either side.
                padded = np.pad(mouth, 2, mode="edge")
                nearby = np.max(np.stack([padded[i:i + len(mouth)] for i in range(5)]), axis=0)
                moving = nearby > MOUTH_MOTION_DELTA
                voiced_ratio = float(moving[voiced].mean()) if voiced.any() else 0.0
                result.update({
                    "voiced_seconds": round(float(voiced.sum()) / nominal_fps, 3),
                    "voiced_mouth_motion_ratio": round(voiced_ratio, 4),
                    "mouth_change_while_voiced": round(float(mouth[voiced].mean()), 4) if voiced.any() else None,
                    "mouth_change_while_quiet": round(float(mouth[quiet].mean()), 4) if quiet.any() else None,
                })
                if voiced.sum() / nominal_fps >= 0.5 and voiced_ratio < MIN_VOICED_MOUTH_RATIO:
                    issues.append(
                        f"The presenter's mouth moves in only {voiced_ratio * 100:.0f}% of the frames where "
                        f"narration is audible (minimum {MIN_VOICED_MOUTH_RATIO * 100:.0f}%)."
                    )
            # The renderer records the mouth shape it applied on every frame. The picture
            # must change when that track changes: this catches frames that were dropped,
            # repeated or shifted between rendering and the finished file.
            track = [float(value) for value in (mouth_track or []) if isinstance(value, (int, float))]
            if len(track) >= 30 and abs(len(track) - count) <= 1:
                intended = np.abs(np.diff(np.array(track[:count])))
                size = min(len(intended), len(mouth))
                a, b = mouth[:size], intended[:size]
                best = (-1.0, 0)
                for lag in range(-6, 7):
                    x, y = (a[lag:], b[:size - lag]) if lag >= 0 else (a[:lag], b[-lag:])
                    if len(x) < 20 or x.std() == 0 or y.std() == 0:
                        continue
                    score = float(np.corrcoef(x, y)[0, 1])
                    if score > best[0]:
                        best = (score, lag)
                result["mouth_track_correlation"] = round(best[0], 3)
                result["mouth_track_offset_frames"] = best[1]
                if best[0] >= 0.25 and abs(best[1]) >= 2:
                    issues.append(
                        f"The picture is {abs(best[1])} frames {'behind' if best[1] > 0 else 'ahead of'} "
                        "the mouth movement that was rendered for the narration."
                    )
                elif best[0] < 0.2:
                    warnings.append("Mouth movement in the picture could not be matched to the rendered mouth track.")
    if not result["mouth_measured"]:
        warnings.append("Mouth movement was not measured: the clip has no recorded mouth position.")
    result.update({"status": "FAIL" if issues else ("REVIEW" if warnings else "PASS"), "issues": issues, "warnings": warnings})
    return result


def read_presenter_metadata(path: str | Path) -> dict[str, Any]:
    """The provenance record written beside a presenter clip, or {} when there is none."""
    media = Path(path)
    sidecar = media.with_suffix(media.suffix + ".source.json")
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def read_render_record(path: str | Path) -> dict[str, Any]:
    """What the offline renderer recorded for a clip (mouth position, mouth track), or {}."""
    try:
        loaded = json.loads(Path(path).with_suffix(".render.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_presenter_metadata(path: str | Path, metadata: dict[str, Any]) -> Path:
    """Persist presenter provenance and technical sync status beside the clip."""
    import os
    import tempfile

    media = Path(path)
    sidecar = media.with_suffix(media.suffix + ".source.json")
    current: dict[str, Any] = {}
    if sidecar.is_file():
        try:
            loaded = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, UnicodeError, json.JSONDecodeError):
            current = {}
    current.update(metadata)
    fd, temp_name = tempfile.mkstemp(prefix=".presenter-source.", suffix=".tmp", dir=str(sidecar.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, sidecar)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return sidecar
