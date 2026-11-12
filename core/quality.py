"""Free, local FFmpeg-based video quality checks for rendered exports."""
import json
import re
import subprocess
import tempfile
from pathlib import Path


def _run_json(command):
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return json.loads(result.stdout or "{}")


def _ffmpeg():
    """Resolved FFmpeg binary (honours FFMPEG_BINARY); plain name as a last resort."""
    try:
        from core.ffmpeg_utils import ffmpeg_bin
        return ffmpeg_bin()
    except Exception:
        return "ffmpeg"


def _ffprobe():
    try:
        from core.ffmpeg_utils import ffprobe_bin
        return ffprobe_bin()
    except Exception:
        return "ffprobe"


def _seconds(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _average_hash(path):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            pixels = list(ImageOps.grayscale(image).resize((8, 8)).tobytes())  # mode L: one byte per pixel
        mean = sum(pixels) / max(len(pixels), 1)
        return sum((1 << i) for i, value in enumerate(pixels) if value >= mean)
    except Exception:
        return None


def _hamming(left, right):
    return (left ^ right).bit_count()


def inspect_video(video_path, subtitle_path=None, sample_count=12, profile="auto", body_seconds=None):
    """Return a JSON-safe report. Requires ffprobe/ffmpeg; never changes the video.

    ``profile`` is ``"shorts"`` (9:16), ``"longform"`` (16:9) or ``"auto"``
    (decided from the encoded frame). Previously every vertical Short was
    judged against landscape rules and could never reach PASS.
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    probe = _run_json([
        _ffprobe(), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(video_path)
    ])
    streams = probe.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _seconds(probe.get("format", {}).get("duration"))
    size_bytes = video_path.stat().st_size
    issues, warnings, metrics = [], [], {
        "duration_seconds": round(duration, 2),
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "has_video": bool(video_stream),
        "has_audio": bool(audio_stream),
    }

    if not video_stream:
        issues.append("No video stream found.")
    else:
        width, height = int(video_stream.get("width") or 0), int(video_stream.get("height") or 0)
        metrics.update({"width": width, "height": height, "video_codec": video_stream.get("codec_name"),
                        "pixel_format": video_stream.get("pix_fmt")})
        requested = str(profile or "auto").strip().lower()
        if requested not in {"shorts", "longform"}:
            requested = "shorts" if height > width else "longform"
        metrics["profile"] = requested
        if requested == "shorts":
            if width < 720 or height < 1280:
                warnings.append(f"Low video resolution for a Short: {width}×{height}.")
            if width and height and abs(width / height - 9 / 16) > 0.02:
                warnings.append("Unexpected aspect ratio for a vertical 9:16 Short.")
            if duration > 180:
                warnings.append(f"Runtime {duration:.0f}s exceeds the 3-minute YouTube Shorts limit.")
            elif 0 < duration < 8:
                warnings.append(f"Runtime {duration:.1f}s is too short for a publishable Short.")
        else:
            if width < 1280 or height < 720:
                warnings.append(f"Low video resolution: {width}×{height}.")
            if width and height and width / height < 1.2:
                warnings.append("Unexpected aspect ratio for a landscape long-form export.")
        if video_stream.get("codec_name") and video_stream.get("codec_name") != "h264":
            warnings.append(f"Video codec is {video_stream.get('codec_name')}, not H.264.")
        if video_stream.get("pix_fmt") and video_stream.get("pix_fmt") != "yuv420p":
            warnings.append(f"Pixel format {video_stream.get('pix_fmt')} may not play on all devices.")
        video_seconds = _seconds(video_stream.get("duration"))
        audio_seconds = _seconds((audio_stream or {}).get("duration"))
        if video_seconds and audio_seconds:
            metrics["av_duration_difference_seconds"] = round(abs(video_seconds - audio_seconds), 3)
            if abs(video_seconds - audio_seconds) > 1.0:
                warnings.append(
                    f"Audio ({audio_seconds:.2f}s) and video ({video_seconds:.2f}s) streams differ in length.")
    if not duration:
        issues.append("Container duration could not be read; the file may be truncated.")
    if not audio_stream:
        issues.append("No audio stream found.")
    else:
        metrics["audio_codec"] = audio_stream.get("codec_name")
        metrics["audio_sample_rate"] = audio_stream.get("sample_rate")

    black_events, silence_events = [], []
    if video_stream and duration:
        command = [
            _ffmpeg(), "-hide_banner", "-nostats", "-i", str(video_path),
            "-vf", "blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        log = (result.stderr or "") + (result.stdout or "")
        for match in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", log):
            black_events.append({"start": float(match.group(1)), "end": float(match.group(2)),
                                 "duration": float(match.group(3))})
        if result.returncode != 0:
            warnings.append("Black-frame scan could not complete.")
    if audio_stream and duration:
        command = [
            _ffmpeg(), "-hide_banner", "-nostats", "-i", str(video_path),
            "-vn", "-af", "silencedetect=noise=-45dB:d=1.0", "-f", "null", "-"
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        log = (result.stderr or "") + (result.stdout or "")
        starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
        ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
        for index, start in enumerate(starts):
            end = ends[index] if index < len(ends) else duration
            if end > start:
                silence_events.append({"start": start, "end": end, "duration": end - start})
        if result.returncode != 0:
            warnings.append("Audio silence scan could not complete.")

    if audio_stream and duration:
        # A track that exists but carries no audible narration is a failed render.
        result = subprocess.run([
            _ffmpeg(), "-hide_banner", "-nostats", "-i", str(video_path),
            "-vn", "-af", "volumedetect", "-f", "null", "-"
        ], capture_output=True, text=True, timeout=600)
        match = re.search(r"mean_volume:\s*(-?[\d.]+|-inf)\s*dB", (result.stderr or "") + (result.stdout or ""))
        if match:
            mean_volume = float("-inf") if match.group(1) == "-inf" else float(match.group(1))
            metrics["mean_volume_db"] = None if mean_volume == float("-inf") else mean_volume
            if mean_volume < -50:
                issues.append("Audio track is effectively silent (mean volume below -50 dB).")
            elif mean_volume < -35:
                warnings.append(f"Audio is very quiet (mean volume {mean_volume:.1f} dB).")

    # A source-credit slate appended after the programme is dark, silent text by
    # design.  Only the programme body counts towards black/silence warnings.
    if body_seconds and 0 < float(body_seconds) < duration:
        body_end = float(body_seconds) + 0.25
        metrics["end_card_seconds"] = round(duration - float(body_seconds), 2)

        def _body_only(events):
            kept = []
            for item in events:
                if item["start"] >= body_end:
                    continue
                end = min(item["end"], body_end)
                kept.append({"start": item["start"], "end": end, "duration": end - item["start"]})
            return kept

        black_events, silence_events = _body_only(black_events), _body_only(silence_events)
        # blackdetect needs 0.5 s and silencedetect 1.0 s; a clipped remainder is not an event.
        black_events = [item for item in black_events if item["duration"] >= 0.5]
        silence_events = [item for item in silence_events if item["duration"] >= 1.0]

    black_duration = sum(item["duration"] for item in black_events)
    silence_duration = sum(item["duration"] for item in silence_events)
    metrics.update({
        "black_intervals": black_events,
        "black_seconds": round(black_duration, 2),
        "silence_intervals": silence_events,
        "silence_seconds": round(silence_duration, 2),
    })
    if duration and black_duration / duration > 0.03:
        warnings.append("More than 3% of the export is detected as black.")
    if silence_duration > max(5.0, duration * 0.08):
        warnings.append("Long or frequent near-silent audio intervals detected.")

    duplicate_pairs = []
    if video_stream and duration > 3 and sample_count > 1:
        with tempfile.TemporaryDirectory(prefix="ai_studio_qc_") as temp_dir:
            hashes = []
            for index in range(sample_count):
                timestamp = duration * (index + 0.5) / sample_count
                frame = Path(temp_dir) / f"frame_{index:02d}.jpg"
                extract = subprocess.run([
                    _ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
                    "-i", str(video_path), "-frames:v", "1", str(frame)
                ], capture_output=True, text=True, timeout=60)
                if extract.returncode == 0 and frame.exists():
                    value = _average_hash(frame)
                    if value is not None:
                        hashes.append((index, value))
            for left in range(len(hashes)):
                for right in range(left + 1, len(hashes)):
                    if _hamming(hashes[left][1], hashes[right][1]) <= 2:
                        duplicate_pairs.append([hashes[left][0], hashes[right][0]])
    metrics["sampled_frames"] = sample_count
    metrics["near_duplicate_frame_pairs"] = duplicate_pairs
    if len(duplicate_pairs) >= max(3, sample_count // 3):
        warnings.append("Many sampled frames look nearly identical; check for repetitive visuals.")

    subtitle_info = {"provided": bool(subtitle_path), "exists": False, "dialogue_events": 0}
    if subtitle_path:
        subtitle = Path(subtitle_path)
        subtitle_info["exists"] = subtitle.is_file()
        if subtitle.is_file():
            try:
                text = subtitle.read_text(encoding="utf-8-sig", errors="replace")
                events = re.findall(
                    r"^Dialogue:\s*\d+,([^,]+),([^,]+),",
                    text, flags=re.MULTILINE
                )
                subtitle_info["dialogue_events"] = len(events)
                def ass_time(value):
                    h, m, rest = value.split(":")
                    sec, centis = rest.split(".")
                    return int(h) * 3600 + int(m) * 60 + int(sec) + int(centis) / 100
                ends = [ass_time(end) for _, end in events]
                subtitle_info["last_caption_end_seconds"] = round(max(ends), 2) if ends else 0
                if not events:
                    warnings.append("Subtitle file contains no dialogue events.")
                if duration and ends and max(ends) > duration + 2:
                    warnings.append("Subtitle events extend beyond the video duration.")
            except Exception as exc:
                warnings.append(f"Could not inspect subtitle timing: {exc}")
        else:
            warnings.append("Subtitle file is missing.")
    metrics["subtitles"] = subtitle_info

    deductions = 20 * len(issues) + 8 * len(warnings)
    score = max(0, min(100, 100 - deductions))
    return {
        "video_path": str(video_path),
        "status": "FAIL" if issues else ("REVIEW" if warnings else "PASS"),
        "score": score,
        "issues": issues,
        "warnings": warnings,
        "metrics": metrics,
    }


def write_report(report, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
