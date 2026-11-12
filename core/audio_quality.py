"""FFmpeg-based loudness, peak, clipping and silence checks for documentary audio."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from core.ffmpeg_utils import ffmpeg_bin  # honours FFMPEG_BINARY


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
    """Count of full-scale samples reported by ``astats``; ``None`` when unknown.

    FFmpeg's ``astats`` does not print an out-of-range counter, so the old
    pattern never matched and every documentary carried a "could not be
    completed" warning.  The Overall block is used instead: samples sitting at
    full scale (peak at 0 dBFS) are the ones that clipped.
    """
    match = re.search(r"Number of samples out of range:\s*(\d+)", log, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    overall = log.rsplit("Overall", 1)[-1] if "Overall" in log else ""
    peak = re.search(r"Peak level dB:\s*(-?inf|-?[\d.]+)", overall, flags=re.IGNORECASE)
    if not peak:
        return None
    if peak.group(1).lower().endswith("inf"):
        return 0
    if float(peak.group(1)) < -0.01:
        return 0
    count = re.search(r"Peak count:\s*([\d.]+)", overall, flags=re.IGNORECASE)
    return max(1, int(float(count.group(1)))) if count else 1


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
                  true_peak_target: float = -1.5, lra_target: float = 11.0,
                  body_seconds: float | None = None) -> dict[str, Any]:
    """Measure integrated loudness, true peak, clipped samples and long silences.

    ``body_seconds`` is the programme length before a silent credit slate; silence
    that begins there is the slate, not a gap in the narration.
    """
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio source video not found: {path}")
    issues, warnings = [], []
    loud_command = [
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
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
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "astats=metadata=1:reset=0", "-f", "null", "-",
    ], capture_output=True, text=True, check=False, timeout=600)
    astats_log = (astats.stderr or "") + (astats.stdout or "")
    clipped = _clipped_samples(astats_log)

    silence = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "silencedetect=noise=-50dB:d=1.0", "-f", "null", "-",
    ], capture_output=True, text=True, check=False, timeout=600)
    silence_log = (silence.stderr or "") + (silence.stdout or "")
    intervals = _silence_intervals(silence_log)
    if body_seconds and float(body_seconds) > 0:
        intervals = [item for item in intervals if item["start"] < float(body_seconds) - 0.5]

    integrated = loudness.get("input_i")
    true_peak = loudness.get("input_tp")
    lra = loudness.get("input_lra")
    if true_peak is not None and true_peak > -0.5:
        issues.append("Audio true peak is above -0.5 dBFS; clipping risk is high.")
    elif true_peak is not None and true_peak > -1.0:
        warnings.append("Audio true peak is above the recommended -1.0 dBFS ceiling.")
    if clipped is not None and clipped > 0:
        issues.append(f"FFmpeg detected {clipped} out-of-range audio samples.")
    elif clipped is None or astats.returncode != 0:
        warnings.append("Audio clipping statistics could not be completed.")
    if silence.returncode != 0:
        warnings.append("Audio silence scan could not be completed.")
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
        "human_review_status": "pending",
        "reviewed_at": None,
        "reviewer_notes": "",
        "notice": "Loudness and peak measurements are technical checks. Listen to the complete export on headphones and speakers before release.",
    }


def mark_audio_reviewed(build_dir: str | Path, reviewer_notes: str = "") -> dict[str, Any]:
    """Record an explicit human listening review after inspecting the audio report."""
    root = Path(build_dir)
    path = root / "audio_quality_report.json"
    if not path.is_file():
        raise FileNotFoundError(f"Audio quality report is missing: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("status") not in {"PASS", "REVIEW"}:
        raise ValueError("Audio report is invalid or contains blocking failures; fix and rebuild first.")
    report["human_review_status"] = "reviewed"
    report["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["reviewer_notes"] = str(reviewer_notes or "")[:2000]
    fd, temp_name = tempfile.mkstemp(prefix=".audio-review.", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return report


def assert_audio_quality_ready(build_dir: str | Path) -> dict[str, Any]:
    """Require a passing/reviewable audio report and explicit human listening review."""
    path = Path(build_dir) / "audio_quality_report.json"
    if not path.is_file():
        raise ValueError(
            f"Audio quality report is missing from {path.parent}. Rebuild the documentary with audio mastering QA."
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read audio quality report {path}: {exc}") from exc
    if not isinstance(report, dict) or report.get("status") not in {"PASS", "REVIEW"}:
        raise ValueError("Audio QA has blocking failures or an invalid report. Fix the audio and rebuild.")
    if report.get("human_review_status") != "reviewed":
        raise ValueError("Audio listening review is pending. Listen to the complete documentary and mark audio reviewed in the dashboard.")
    return report
