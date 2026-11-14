"""Add required 3D Talking Avatar clips to a reviewed news Short.

This post-render stage is intentionally fail-closed: it never substitutes a
2D portrait when avatar generation fails. Original narration audio remains
the master audio track; the avatar clip audio is discarded after lip-sync
recording to avoid doubling the voice.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin, tool_timeout


def _duration(path: Path) -> float:
    value = subprocess.check_output([
        ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], text=True, timeout=PROBE_TIMEOUT).strip()
    return float(value)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=tool_timeout())


def add_3d_presenter(
    output_dir: str | Path,
    *,
    gender: str = "female",
    repo_root: str | Path | None = None,
) -> dict:
    """Generate one narration-matched 3D avatar clip per scene and overlay it.

    Expects render_review_copy() outputs, including build/voice_NN/voice.wav
    and production_manifest.json. The avatar is shown as a portrait PIP over
    the approved scene footage for the matching narration beat.
    """
    out = Path(output_dir).resolve()
    root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[1]
    manifest_path = out / "production_manifest.json"
    video_path = out / "review_short.mp4"
    if not manifest_path.is_file() or not video_path.is_file():
        raise FileNotFoundError("A completed review render and production_manifest.json are required.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shots = manifest.get("shots")
    if not isinstance(shots, list) or not 4 <= len(shots) <= 6:
        raise ValueError("3D presenter integration requires the reviewed 4–6 scene manifest.")

    build = out / "build"
    audio_dir = build / "avatar_scene_audio"
    clips_dir = build / "avatar_clips"
    audio_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    for index, shot in enumerate(shots, start=1):
        source_audio = build / f"voice_{index - 1:02d}" / "voice.wav"
        if not source_audio.is_file() or source_audio.stat().st_size < 1024:
            raise FileNotFoundError(f"Scene {index} narration audio is missing: {source_audio}")
        target_audio = audio_dir / f"chapter_{index:02d}.wav"
        shutil.copy2(source_audio, target_audio)

    env = os.environ.copy()
    env["AVATAR_AUDIO_DIR"] = str(audio_dir)
    env["AVATAR_OUTPUT_DIR"] = str(clips_dir)
    env["AVATAR_GENDER"] = "male" if str(gender).lower() == "male" else "female"
    runner = root / "avatar_studio" / "generate-clips.mjs"
    if not runner.is_file():
        raise FileNotFoundError(f"3D avatar recording runner is missing: {runner}")
    _run(["node", str(runner), str(len(shots))], cwd=root, env=env)

    command = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video_path)]
    clips: list[tuple[Path, float, float]] = []
    for index, shot in enumerate(shots, start=1):
        clip = clips_dir / f"presenter_section_{index:02d}.mp4"
        if not clip.is_file() or clip.stat().st_size < 10000:
            raise RuntimeError(f"3D avatar clip {index} was not generated.")
        streams = subprocess.check_output([
            ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(clip)
        ], text=True, timeout=PROBE_TIMEOUT).splitlines()
        if "video" not in streams or "audio" not in streams:
            raise RuntimeError(f"3D avatar clip {index} must contain decodable video and narration audio.")
        start = float(shot["start_seconds"])
        duration = float(shot["duration_seconds"])
        clip_duration = _duration(clip)
        if clip_duration < duration * 0.90:
            raise RuntimeError(
                f"3D avatar clip {index} is too short ({clip_duration:.2f}s) "
                f"for its narration beat ({duration:.2f}s)."
            )
        command += ["-i", str(clip)]
        clips.append((clip, start, duration))

    filters = ["[0:v]format=yuv420p[base]"]
    current = "base"
    for index, (_clip, start, duration) in enumerate(clips, start=1):
        label = f"avatar{index}"
        next_label = f"composite{index}"
        input_index = index
        filters.append(
            f"[{input_index}:v]setpts=PTS-STARTPTS+{start:.3f}/TB,"
            "scale=-1:720:flags=lanczos,crop=405:720:(iw-405)/2:0,"
            "scale=320:568:flags=lanczos,setsar=1,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.92:t=4"
            f"[{label}]"
        )
        filters.append(
            f"[{current}][{label}]overlay=x=W-w-48:y=H-h-360:"
            f"eof_action=pass:enable='between(t,{start:.3f},{start + duration:.3f})'"
            f"[{next_label}]"
        )
        current = next_label

    final_tmp = out / "review_short_3d_tmp.mp4"
    command += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{current}]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
        "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
        str(final_tmp),
    ]
    _run(command, cwd=root)
    if not final_tmp.is_file() or final_tmp.stat().st_size < 100000:
        raise RuntimeError("3D-presenter composite MP4 was not created or is unexpectedly small.")
    os.replace(final_tmp, video_path)

    manifest["presenter_mode"] = "required_3d_talking_avatar_picture_in_picture"
    manifest["presenter_gender"] = env["AVATAR_GENDER"]
    manifest["presenter_clips"] = [
        {
            "scene": index,
            "path": str(clips_dir / f"presenter_section_{index:02d}.mp4"),
            "start_seconds": round(start, 3),
            "duration_seconds": round(duration, 3),
            "audio_used_for_lipsync": True,
            "clip_audio_in_final_mix": False,
        }
        for index, (_clip, start, duration) in enumerate(clips, start=1)
    ]
    manifest["visual_style"] = "approved_scene_footage_with_required_3d_talking_avatar_pip"
    manifest["video_path"] = str(video_path)
    manifest["avatar_integration"] = {
        "status": "generated_and_composited",
        "fallback_to_2d_presenter": False,
        "human_lipsync_review_required": True,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "3d_presenter_integrated",
        "video_path": str(video_path),
        "presenter_clips": len(clips),
        "fallback_to_2d_presenter": False,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate and composite required 3D news presenter clips.")
    parser.add_argument("output_dir")
    parser.add_argument("--gender", choices=("female", "male"), default="female")
    args = parser.parse_args()
    print(json.dumps(add_3d_presenter(args.output_dir, gender=args.gender), ensure_ascii=False, indent=2))
