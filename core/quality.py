"""Free, local FFmpeg-based video quality checks for rendered exports."""
import json
import re
import subprocess
import tempfile
from pathlib import Path


def _run_json(command):
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
    return json.loads(result.stdout or "{}")


def _seconds(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _average_hash(path):
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            pixels = list(ImageOps.grayscale(image).resize((8, 8)).getdata())
        mean = sum(pixels) / max(len(pixels), 1)
        return sum((1 << i) for i, value in enumerate(pixels) if value >= mean)
    except Exception:
        return None


def _hamming(left, right):
    return (left ^ right).bit_count()


def inspect_video(video_path, subtitle_path=None, sample_count=12):
    """Return a JSON-safe report. Requires ffprobe/ffmpeg; never changes the video."""
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    probe = _run_json([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
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
        metrics.update({"width": width, "height": height, "video_codec": video_stream.get("codec_name")})
        if width < 1280 or height < 720:
            warnings.append(f"Low video resolution: {width}×{height}.")
        if width and height and width / height < 1.2:
            warnings.append("Unexpected aspect ratio for a landscape long-form export.")
    if not audio_stream:
        issues.append("No audio stream found.")
    else:
        metrics["audio_codec"] = audio_stream.get("codec_name")
        metrics["audio_sample_rate"] = audio_stream.get("sample_rate")

    black_events, silence_events = [], []
    if video_stream and duration:
        command = [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path),
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
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path),
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
                    "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{timestamp:.3f}",
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
