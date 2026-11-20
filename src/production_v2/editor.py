"""Deterministic FFmpeg editor: scene video, mixed audio, English subtitle track."""
from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactStore
from .config import V2Config
from .errors import RenderError
from .ffmpeg_io import resolve_ffmpeg, run
from .models import NarrationSegment, Scene
from .visuals import VisualAsset


def _segment_command(ffmpeg: str, image: Path, audio: Path, output: Path,
                     duration: float, width: int, height: int, fps: int, *, is_video: bool = False) -> list[str]:
    fade_out = max(0.0, duration - 0.5)
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
        *(["-stream_loop", "-1"] if is_video else ["-loop", "1", "-framerate", str(fps)]),
        "-i", str(image), "-i", str(audio),
        "-t", f"{duration:.3f}",
        "-filter_complex",
        (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
         f"crop={width}:{height},"
         f"zoompan=z='min(zoom+0.0006,1.06)':x='iw/2-(iw/zoom/2)':"
         f"y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps},"
         f"fade=t=in:st=0:d=0.4,fade=t=out:st={fade_out:.3f}:d=0.4,format=yuv420p[v];"
         f"[1:a]apad,atrim=duration={duration:.3f}[a]"),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(output),
    ]


class Editor:
    def render(self, *, scenes: list[Scene], assets: list[VisualAsset],
               segments: list[NarrationSegment], mixed_audio: Path, subtitles: Path,
               store: ArtifactStore, config: V2Config, content_type: str) -> dict:
        ffmpeg = resolve_ffmpeg(config)
        if not ffmpeg:
            raise RenderError("FFmpeg is required for v2 rendering")
        rules = config.rules_for(content_type)
        by_scene_audio = {s.id: Path(s.audio_path) for s in segments}
        if len(assets) != len(scenes):
            raise RenderError("every scene must have exactly one visual")
        work = store.path("segments")
        work.mkdir(parents=True, exist_ok=True)
        segment_files: list[Path] = []
        for index, (scene, asset) in enumerate(zip(scenes, assets), start=1):
            audio = by_scene_audio.get(scene.id)
            if audio is None or not audio.is_file():
                raise RenderError(f"missing narration audio for {scene.id}")
            target = work / f"segment_{index:02d}.mp4"
            run(_segment_command(ffmpeg, asset.image_path, audio, target,
                                 scene.visual.duration, rules.width, rules.height, rules.fps,
                                 is_video=asset.record.media_type == "video"),
                timeout=config.ffmpeg_timeout_seconds)
            segment_files.append(target)

        concat_list = store.path("concat.txt")
        # FFmpeg resolves concat entries relative to the process CWD, not the list file.
        concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_files), encoding="utf-8")
        joined = store.path("joined.mp4")
        run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(joined)],
            timeout=config.ffmpeg_timeout_seconds)

        final = store.path("final.mp4")
        run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(joined),
             "-i", str(mixed_audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)],
            timeout=config.ffmpeg_timeout_seconds)

        warnings: list[str] = []
        with_subs = store.path("final_with_subs.mp4")
        if subtitles.is_file():
            result = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-i", str(final),
                          "-i", str(subtitles), "-map", "0", "-map", "1", "-c", "copy",
                          "-c:s", "mov_text", str(with_subs)],
                         timeout=config.ffmpeg_timeout_seconds, check=False)
            if result.returncode == 0 and with_subs.is_file():
                with_subs.replace(final)
            else:
                warnings.append("subtitle track could not be muxed; SRT/ASS files remain in artifacts")
        return {"video": str(final), "segments": len(segment_files),
                "warnings": warnings, "resolution": f"{rules.width}x{rules.height}"}
