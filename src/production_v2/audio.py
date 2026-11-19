"""Narration mixing, loudness control and objective audio checks."""
from __future__ import annotations

import math
import shutil
import wave
from pathlib import Path

from .config import V2Config
from .errors import ValidationError
from .ffmpeg_io import resolve_ffmpeg, run
from .models import NarrationSegment


def _read_wav(path: Path) -> tuple[list[int], int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValidationError(f"expected mono 16-bit WAV: {path}")
        rate = handle.getframerate()
        frames = list(struct_frames(handle.readframes(handle.getnframes())))
    return frames, rate


def struct_frames(data: bytes) -> list[int]:
    import array
    values = array.array("h")
    values.frombytes(data)
    return list(values)


def _pack(frames: list[int]) -> bytes:
    import array
    values = array.array("h", frames)
    return values.tobytes()


def concat_segments(segments: list[NarrationSegment], output: Path,
                    *, min_duration: float = 0.0) -> dict:
    """Place each narration clip at its planned timestamp on one mono timeline."""
    if not segments:
        raise ValidationError("no narration segments to mix")
    rate = 16_000
    audio: dict[str, object] = {}
    for segment in segments:
        source = Path(segment.audio_path)
        if source.suffix.lower() != ".wav":
            raise ValidationError("v2 mixing expects WAV narration; convert mp3 first")
        frames, file_rate = _read_wav(source)
        if file_rate != rate:
            raise ValidationError(f"unexpected sample rate {file_rate} in {source.name}")
        audio[segment.id] = frames
    total = max(min_duration, max(s.end for s in segments))
    length = int(total * rate)
    # Quiet, deterministic bed prevents long artificial silence between short
    # narration segments in offline demonstration renders.
    timeline = [int(160 * math.sin(2 * math.pi * 110 * i / rate)) for i in range(length)]
    for segment in segments:
        start = int(segment.start * rate)
        frames = audio.get(segment.id) or []
        for offset, sample in enumerate(frames):
            index = start + offset
            if index >= length:
                break
            timeline[index] = max(timeline[index], sample)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(_pack(timeline))
    return {"path": str(output), "sample_rate": rate, "duration_seconds": round(total, 3)}


def analyze_wav(path: Path) -> dict:
    frames, rate = _read_wav(Path(path))
    if not frames:
        return {"status": "FAIL", "duration_seconds": 0, "issue": "empty audio"}
    peak = max(abs(sample) for sample in frames) / 32768.0
    clipped = sum(1 for sample in frames if abs(sample) >= 32_000)
    rms = math.sqrt(sum(sample * sample for sample in frames) / len(frames)) / 32768.0
    threshold = int(rate * 0.4)
    runs = 0
    current = 0
    longest = 0
    for sample in frames:
        if abs(sample) < 120:
            current += 1
            longest = max(longest, current)
        else:
            if current >= threshold:
                runs += 1
            current = 0
    issues = []
    if peak >= 0.999:
        issues.append("audio may be clipped")
    if clipped > len(frames) * 0.001:
        issues.append("many samples at full scale")
    if longest > rate * 5:
        issues.append("silent interval longer than five seconds")
    return {"status": "PASS" if not issues else "FAIL",
            "duration_seconds": round(len(frames) / rate, 3), "peak": round(peak, 4),
            "rms": round(rms, 5), "clipped_samples": clipped,
            "longest_silence_seconds": round(longest / rate, 3), "issues": issues}


def loudness_normalize(input_wav: Path, output_wav: Path, config: V2Config) -> dict:
    """Apply FFmpeg loudnorm when available; otherwise copy without claiming normalisation."""
    ffmpeg = resolve_ffmpeg(config)
    if not ffmpeg:
        shutil.copyfile(input_wav, output_wav)
        return {"applied": False, "reason": "ffmpeg unavailable; copied unchanged"}
    result = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(input_wav),
                  "-af", f"loudnorm=I={config.target_lufs}:TP={config.true_peak_dbfs}:LRA=11",
                  "-ar", "16000", "-ac", "1", str(output_wav)],
                 timeout=config.ffmpeg_timeout_seconds, check=False)
    if result.returncode != 0:
        shutil.copyfile(input_wav, output_wav)
        return {"applied": False, "reason": "loudnorm unavailable; copied unchanged"}
    return {"applied": True, "target_lufs": config.target_lufs,
            "true_peak_dbfs": config.true_peak_dbfs}
