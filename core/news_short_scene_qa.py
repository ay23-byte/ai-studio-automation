"""Per-scene footage coverage and visual integrity QA for news Shorts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _probe_duration(video: Path) -> float:
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video)
    ], text=True)
    return float(raw.strip())


def _extract_frame(video: Path, timestamp: float, output: Path) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=160:284:flags=area",
        "-q:v", "3", str(output)
    ], capture_output=True, text=True, check=False, timeout=60)
    return result.returncode == 0 and output.is_file() and output.stat().st_size > 300


def _frame_metrics(path: Path) -> dict:
    from PIL import Image, ImageStat
    with Image.open(path) as image:
        gray = image.convert("L")
        stat = ImageStat.Stat(gray)
        return {"mean_luma": round(stat.mean[0], 3),
                "pixel_stddev": round(stat.stddev[0], 3)}


def _frame_difference(first: Path, second: Path) -> float:
    from PIL import Image, ImageChops, ImageStat
    with Image.open(first) as a, Image.open(second) as b:
        a, b = a.convert("L"), b.convert("L")
        return round(ImageStat.Stat(ImageChops.difference(a, b)).mean[0], 4)


def audit_scene_footage(manifest_path: str | Path, video_path: str | Path,
                        report_dir: str | Path | None = None) -> dict:
    manifest_path, video_path = Path(manifest_path), Path(video_path)
    errors, warnings, rows = [], [], []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shots = manifest.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ValueError("Production manifest has no scene list.")
        if not video_path.is_file():
            raise FileNotFoundError(f"Rendered Short is missing: {video_path}")
        video_duration = _probe_duration(video_path)
    except Exception as exc:
        return {"status": "FAIL", "errors": [f"{type(exc).__name__}: {exc}"],
                "warnings": [], "scene_count": 0,
                "human_review_required": True, "publication_ready": False}

    root = Path(report_dir) if report_dir else video_path.parent / "advanced_qa"
    sample_dir = root / "scene_footage_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    previous_end = 0.0

    for index, shot in enumerate(shots, 1):
        scene_errors, scene_warnings, samples = [], [], []
        try:
            start = float(shot["start_seconds"])
            duration = float(shot["duration_seconds"])
            if start < 0 or duration <= 0:
                raise ValueError("Scene start must be non-negative and duration must be positive.")
            end = start + duration
            if end > video_duration + 0.12:
                scene_errors.append(
                    f"Scene ends at {end:.2f}s beyond rendered video duration {video_duration:.2f}s."
                )
            if index > 1:
                gap = start - previous_end
                if gap < -0.08:
                    scene_errors.append(f"Scene overlaps the previous scene by {abs(gap):.2f}s.")
                elif gap > 0.75:
                    scene_warnings.append(f"Scene begins after a {gap:.2f}s timeline gap.")
            previous_end = end

            paths = []
            for sample_index, fraction in enumerate((0.20, 0.50, 0.80), 1):
                timestamp = start + duration * fraction
                target = sample_dir / f"scene_{index:02d}_{sample_index}.jpg"
                if not _extract_frame(video_path, timestamp, target):
                    scene_errors.append(f"Could not extract footage at {timestamp:.2f}s.")
                    continue
                metrics = _frame_metrics(target)
                paths.append(target)
                samples.append({"timestamp_seconds": round(timestamp, 3), **metrics})
                if metrics["mean_luma"] < 5.0 and metrics["pixel_stddev"] < 4.0:
                    scene_errors.append(f"Near-black footage detected at {timestamp:.2f}s.")

            differences = []
            for a, b in zip(paths, paths[1:]):
                differences.append(_frame_difference(a, b))
            if len(paths) == 3 and differences and max(differences) < 0.35:
                scene_warnings.append(
                    "Sampled frames are nearly identical; verify the scene is not unintentionally frozen."
                )
            if len(samples) != 3:
                scene_errors.append("Scene did not produce all three required visual samples.")
            rows.append({
                "scene": index, "start_seconds": round(start, 3),
                "end_seconds": round(end, 3), "duration_seconds": round(duration, 3),
                "samples": samples, "frame_mean_differences": differences,
                "errors": scene_errors, "warnings": scene_warnings,
            })
        except (KeyError, TypeError, ValueError) as exc:
            scene_errors.append(f"Invalid scene timing: {exc}")
            rows.append({"scene": index, "errors": scene_errors, "warnings": scene_warnings,
                         "samples": [], "frame_mean_differences": []})
        errors.extend(f"Scene {index}: {item}" for item in scene_errors)
        warnings.extend(f"Scene {index}: {item}" for item in scene_warnings)

    status = "FAIL" if errors else ("REVIEW" if warnings else "PASS")
    report = {
        "status": status, "video_duration_seconds": round(video_duration, 3),
        "scene_count": len(shots), "sampled_scene_count": sum(len(row.get("samples", [])) == 3 for row in rows),
        "scenes": rows, "errors": errors, "warnings": warnings,
        "human_review_required": True, "publication_ready": False,
        "notice": "Frame sampling can identify black, missing, overlapping, or suspiciously static footage. It cannot judge editorial relevance, factual accuracy, rights, or whether intentional still images are appropriate. Watch the full Short.",
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "scene_footage_qa_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--video", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    video = Path(args.video) if args.video else Path(args.manifest).parent / "review_short.mp4"
    result = audit_scene_footage(args.manifest, video)
    report = Path(args.report) if args.report else video.parent / "advanced_qa" / "scene_footage_qa_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result["status"] == "FAIL")
