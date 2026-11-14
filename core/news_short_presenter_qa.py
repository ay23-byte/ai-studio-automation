"""Scene-by-scene structural and visible-region QA for 3D news presenters.

This verifies clip presence/streams/duration and samples the known presenter PIP
region in the final MP4. Pixel variation is only a visibility heuristic; it
cannot certify correct lip-sync, facial expression, or natural movement.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin


def _probe(path: Path) -> dict:
    raw = subprocess.check_output([
        ffprobe_bin(), "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path)
    ], text=True, timeout=PROBE_TIMEOUT)
    return json.loads(raw)


def _sample_region(video: Path, timestamp: float, target: Path) -> bool:
    # Matches core.news_short_avatar.py: 320x568 PIP at x=W-368, y=H-928.
    result = subprocess.run([
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", str(video),
        "-vf", "crop=320:568:712:992,scale=160:284",
        "-frames:v", "1", str(target)
    ], capture_output=True, text=True, check=False, timeout=60)
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 500


def _image_metrics(path: Path) -> dict:
    from PIL import Image, ImageStat
    image = Image.open(path).convert("RGB")
    stat = ImageStat.Stat(image)
    mean = sum(stat.mean) / 3.0
    stddev = sum(stat.stddev) / 3.0
    return {"mean_luma_proxy": round(mean, 2), "pixel_stddev": round(stddev, 2)}


def audit_presenter(manifest_path: str | Path, video_path: str | Path,
                    report_dir: str | Path | None = None) -> dict:
    manifest_path, video_path = Path(manifest_path), Path(video_path)
    errors, warnings, scenes = [], [], []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shots = manifest.get("shots")
        clips = manifest.get("presenter_clips")
        if not isinstance(shots, list) or not shots:
            raise ValueError("Production manifest has no scenes.")
        if manifest.get("presenter_mode") != "required_3d_talking_avatar_picture_in_picture":
            raise ValueError("Manifest does not declare the required 3D presenter mode.")
        if not isinstance(clips, list) or len(clips) != len(shots):
            raise ValueError(f"Expected one presenter clip per scene ({len(shots)}); found {len(clips) if isinstance(clips, list) else 0}.")
        if not video_path.is_file():
            raise FileNotFoundError(f"Final review MP4 is missing: {video_path}")
    except Exception as exc:
        return {"status": "FAIL", "errors": [f"{type(exc).__name__}: {exc}"],
                "warnings": [], "human_review_required": True,
                "presenter_visibility_verified": False}

    root = Path(report_dir) if report_dir else video_path.parent / "advanced_qa"
    samples_dir = root / "presenter_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    for index, (shot, clip_info) in enumerate(zip(shots, clips), 1):
        scene_errors = []
        try:
            clip = Path(clip_info["path"])
            if not clip.is_absolute():
                clip = manifest_path.parent / clip
            if not clip.is_file() or clip.stat().st_size < 10000:
                raise FileNotFoundError(f"Presenter clip {index} is missing or too small: {clip}")
            probe = _probe(clip)
            streams = probe.get("streams", [])
            video_streams = [s for s in streams if s.get("codec_type") == "video"]
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            if len(video_streams) != 1 or len(audio_streams) != 1:
                raise ValueError(f"Presenter clip {index} must contain one video and one audio stream.")
            clip_duration = float((probe.get("format") or {}).get("duration") or 0)
            expected_duration = float(shot["duration_seconds"])
            if clip_duration < expected_duration * 0.90 or clip_duration > expected_duration * 1.20:
                scene_errors.append(
                    f"Presenter clip duration {clip_duration:.2f}s is outside tolerance for scene duration {expected_duration:.2f}s."
                )
            if abs(float(clip_info.get("duration_seconds", expected_duration)) - expected_duration) > 0.06:
                scene_errors.append("Manifest presenter duration does not match scene duration.")
            start = float(shot["start_seconds"])
            samples = []
            for part, fraction in enumerate((0.25, 0.50, 0.75), 1):
                target = samples_dir / f"scene_{index:02d}_{part}.jpg"
                timestamp = start + expected_duration * fraction
                if not _sample_region(video_path, timestamp, target):
                    scene_errors.append(f"Could not sample presenter PIP region at {timestamp:.2f}s.")
                    continue
                metrics = _image_metrics(target)
                samples.append({"timestamp_seconds": round(timestamp, 2), **metrics})
                if metrics["mean_luma_proxy"] < 8 or metrics["pixel_stddev"] < 2:
                    scene_errors.append(f"Presenter PIP region appears black or visually static at {timestamp:.2f}s.")
            scenes.append({
                "scene": index, "clip": str(clip),
                "clip_duration_seconds": round(clip_duration, 3),
                "expected_scene_duration_seconds": round(expected_duration, 3),
                "samples": samples, "errors": scene_errors,
                "clip_streams_verified": True,
            })
        except Exception as exc:
            scene_errors.append(f"{type(exc).__name__}: {exc}")
            scenes.append({"scene": index, "errors": scene_errors,
                           "clip_streams_verified": False, "samples": []})
        errors.extend(f"Scene {index}: {item}" for item in scene_errors)

    if scenes and all(len(item.get("samples", [])) == 3 for item in scenes) and not errors:
        warnings.append("Automated PIP sampling passed; inspect the full video for actual presenter framing, lip-sync and natural motion.")
    status = "FAIL" if errors else "REVIEW"
    return {
        "status": status, "scene_count": len(shots),
        "presenter_clip_count": len(clips), "scenes": scenes,
        "errors": errors, "warnings": warnings,
        "presenter_visibility_verified": status == "REVIEW" and all(
            len(item.get("samples", [])) == 3 for item in scenes
        ),
        "human_review_required": True,
        "publication_ready": False,
        "notice": "Pixel sampling can detect missing, black or visually static PIP regions. It cannot prove correct lip-sync, facial animation, natural movement or editorial quality. Watch the complete Short.",
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--video", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    video = Path(args.video) if args.video else Path(args.manifest).parent / "review_short.mp4"
    result = audit_presenter(args.manifest, video)
    report = Path(args.report) if args.report else video.parent / "advanced_qa" / "presenter_visibility_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result["status"] == "FAIL")
