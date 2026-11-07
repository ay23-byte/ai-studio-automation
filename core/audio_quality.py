"""FFmpeg-based loudness, peak, clipping and silence checks for documentary audio."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _loudnorm_data(log: str) -> dict[str, Any]:
    match = re.search(r'\{\s*"input_i".*?\}', log, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {key: _number(payload.get(key)) for key in
            ("input_i", "input_tp", "input_lra", "input_thresh",
             "output_i", "output_tp", "output_lra", "output_thresh", "target_offset")}


def _clipped_samples(log: str) -> int | None:
    match = re.search(r"Number of samples out of range:\s*(\d+)", log, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _silence_intervals(log: str) -> list[dict[str, float]]:
    starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", log)]
    rows = []
    for index, start in enumerate(starts):
        if index < len(ends) and ends[index] > start:
            rows.append({"start": round(start, 3), "end": round(ends[index], 3),
                         "duration": round(ends[index] - start, 3)})
    return rows


def inspect_audio(video_path: str | Path, target_lufs: float = -16.0,
                  true_peak_target: float = -1.5, lra_target: float = 11.0) -> dict[str, Any]:
    """Measure integrated loudness, true peak, clipped samples and long silences."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio source video not found: {path}")
    issues, warnings = [], []
    loud_command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"loudnorm=I={target_lufs}:TP={true_peak_target}:LRA={lra_target}:print_format=json",
        "-f", "null", "-",
    ]
    loud = subprocess.run(loud_command, capture_output=True, text=True, check=False, timeout=600)
    log = (loud.stderr or "") + (loud.stdout or "")
    loudness = _loudnorm_data(log)
    if loud.returncode != 0 or not loudness or loudness.get("input_i") is None:
        return {
            "status": "ERROR", "issues": ["FFmpeg could not measure integrated loudness; audio QA is incomplete."],
            "warnings": [], "metrics": {}, "target": {"integrated_lufs": target_lufs,
                                                         "true_peak_dbfs": true_peak_target},
        }

    astats = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "astats=metadata=1:reset=0", "-f", "null", "-",
    ], capture_output=True, text=True, check=False, timeout=600)
    astats_log = (astats.stderr or "") + (astats.stdout or "")
    clipped = _clipped_samples(astats_log)

    silence = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "silencedetect=noise=-50dB:d=1.0", "-f", "null", "-",
    ], capture_output=True, text=True, check=False, timeout=600)
    silence_log = (silence.stderr or "") + (silence.stdout or "")
    intervals = _silence_intervals(silence_log)

    integrated = loudness.get("input_i")
    true_peak = loudness.get("input_tp")
    lra = loudness.get("input_lra")
    if true_peak is not None and true_peak > -0.5:
        issues.append("Audio true peak is above -0.5 dBFS; clipping risk is high.")
    elif true_peak is not None and true_peak > -1.0:
        warnings.append("Audio true peak is above the recommended -1.0 dBFS ceiling.")
    if clipped is not None and clipped > 0:
        issues.append(f"FFmpeg detected {clipped} out-of-range audio samples.")
    if integrated is not None and abs(integrated - target_lufs) > 2.0:
        warnings.append(
            f"Integrated loudness is {integrated:.1f} LUFS; target is {target_lufs:.1f} LUFS."
        )
    if lra is not None and (lra < 2.0 or lra > 18.0):
        warnings.append(f"Loudness range is {lra:.1f} LU; inspect dynamics and consistency.")
    long_silence = [item for item in intervals if item["duration"] >= 3.5]
    if long_silence:
        warnings.append("Long near-silent intervals detected; confirm they are intentional pauses.")

    metrics = {
        "integrated_lufs": integrated,
        "true_peak_dbfs": true_peak,
        "loudness_range_lu": lra,
        "clipped_samples": clipped,
        "silence_intervals": intervals,
        "long_silence_count": len(long_silence),
        "loudnorm_output_i": loudness.get("output_i"),
        "loudnorm_output_tp": loudness.get("output_tp"),
    }
    return {
        "status": "FAIL" if issues else ("REVIEW" if warnings or astats.returncode or silence.returncode else "PASS"),
        "target": {"integrated_lufs": target_lufs, "true_peak_dbfs": true_peak_target,
                   "loudness_range_lu": lra_target},
        "metrics": metrics, "issues": issues, "warnings": warnings,
        "notice": "Loudness and peak measurements are technical checks. Listen to the complete export on headphones and speakers before release.",
    }
