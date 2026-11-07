"""Render human-approved news Shorts through the existing TTS and FFmpeg renderer.

This stage intentionally stops at a review MP4. It never uploads or publishes.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any

from core.asset_licensing import build_licence_register

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MEDIA = {"image", "video"}


def validate_render_inputs(script: dict[str, Any], approval: dict[str, Any] | None = None) -> list[dict]:
    if script.get("editorial_status") != "approved_for_production":
        raise ValueError("Script must be explicitly marked approved_for_production after human script/fact review.")
    if not isinstance(approval, dict) or approval.get("status") != "approved":
        raise ValueError("A separate production approval JSON with status='approved' is required.")
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 6:
        raise ValueError("A news Short must contain 4–6 reviewed scenes.")
    known_sources = {str(s.get("source_id")) for s in script.get("sources", []) if s.get("source_id")}
    for index, scene in enumerate(scenes, 1):
        if not str(scene.get("say") or "").strip():
            raise ValueError(f"Scene {index} has no narration.")
        if not str(scene.get("caption") or "").strip():
            raise ValueError(f"Scene {index} has no reviewed caption.")
        refs = scene.get("source_ids", [])
        if not isinstance(refs, list) or not refs or not set(map(str, refs)).issubset(known_sources):
            raise ValueError(f"Scene {index} must cite known source_ids.")
        media_type = scene.get("asset_type")
        if media_type not in ALLOWED_MEDIA:
            raise ValueError(f"Scene {index} asset_type must be image or video.")
        path = Path(str(scene.get("asset_path") or "")).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Scene {index} asset does not exist: {path}")
        sidecar = path.with_suffix(path.suffix + ".source.json")
        if not sidecar.is_file():
            raise ValueError(f"Scene {index} asset is missing its provenance sidecar: {sidecar}")
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Scene {index} provenance sidecar is invalid JSON.") from exc
        if not isinstance(metadata, dict) or not metadata.get("provider"):
            raise ValueError(f"Scene {index} sidecar needs a provider.")
        project_graphic = (
            metadata.get("provider") == "LabLens/AI Studio local graphic renderer"
            and metadata.get("asset_type") == "programmatic_graphic"
            and metadata.get("ai_generated") is False
        )
        if project_graphic:
            if not metadata.get("licence") or not metadata.get("font_licence"):
                raise ValueError(f"Scene {index} project graphic needs project and font licence labels.")
            if not metadata.get("font_licence_url") or not metadata.get("attribution_text"):
                raise ValueError(f"Scene {index} project graphic needs font licence URL and attribution.")
        elif not (metadata.get("page_url") or metadata.get("url")):
            raise ValueError(f"Scene {index} sidecar needs page_url/url.")
        if str(metadata.get("asset_type", "")).lower().startswith("ai_generated"):
            if not scene.get("ai_disclosure") or not metadata.get("terms_reviewed"):
                raise ValueError(f"Scene {index} AI asset needs ai_disclosure=true and terms_reviewed=true.")
        else:
            if not metadata.get("licence") and not metadata.get("license"):
                raise ValueError(f"Scene {index} real asset needs an explicit licence label.")
            has_licence_url = metadata.get("licence_url") or metadata.get("license_url")
            if not has_licence_url and not (project_graphic and metadata.get("font_licence_url")):
                raise ValueError(f"Scene {index} real asset needs a licence URL.")
    return scenes


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _subtitle_text(narration: str, width: int = 42) -> str:
    """Create readable Hindi subtitles from the spoken narration, not English graphic copy."""
    clean = str(narration).replace("\\N", " ").replace("\n", " ")
    clean = clean.replace("{", "").replace("}", "").strip()
    return textwrap.fill(clean, width=width, break_long_words=False, break_on_hyphens=False)


def _write_ass(scenes: list[dict], durations: list[float], output: Path) -> None:
    rows = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "WrapStyle: 2", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Compact, boxed Hindi subtitles sit in the lower safe band instead of over the
        # center of the project graphics. Noto Sans Devanagari is installed in CI.
        "Style: News,Noto Sans Devanagari,46,&H00FFFFFF,&H0000D7FF,&H00101010,&H78000000,0,0,0,0,100,100,0,0,3,2,1,2,90,90,300,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    cursor = 0.0
    for scene, duration in zip(scenes, durations):
        subtitle = _subtitle_text(scene["say"])
        rows.append(f"Dialogue: 0,{_ass_time(cursor)},{_ass_time(cursor + duration)},News,,0,0,0,,{subtitle}")
        cursor += duration + 0.25
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _render_media_timeline(scenes: list[dict], durations: list[float], output: Path, workdir: Path) -> dict:
    """Assemble the actual approved scene media into a vertical 30fps timeline."""
    segments = []
    report = []
    for index, (scene, duration) in enumerate(zip(scenes, durations), start=1):
        source = Path(str(scene["asset_path"])).expanduser().resolve()
        kind = scene["asset_type"]
        segment = workdir / f"visual_{index:02d}.mp4"
        common = [
            "ffmpeg", "-y", "-loglevel", "error",
        ]
        if kind == "video":
            # Loop only when a source clip is shorter than the narration beat;
            # no audio from stock footage is carried into the final mix.
            args = common + [
                "-stream_loop", "-1", "-i", str(source),
                "-t", f"{duration:.3f}",
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=1080:1920,setsar=1,fps=30,"
                "eq=contrast=1.035:saturation=0.92:brightness=0.005,"
                "vignette=PI/6,noise=alls=3:allf=t",
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", "30", "-t", f"{duration:.3f}", str(segment),
            ]
        else:
            frames = max(1, round(duration * 30))
            args = common + [
                "-loop", "1", "-i", str(source), "-t", f"{duration:.3f}",
                "-vf",
                f"scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop=2160:3840,zoompan=z='min(1.12,1+0.12*on/{frames})':"
                f"d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s=1080x1920:fps=30,setsar=1,"
                "eq=contrast=1.035:saturation=0.92:brightness=0.005,"
                "vignette=PI/6,noise=alls=3:allf=t",
                "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", "30", "-t", f"{duration:.3f}", str(segment),
            ]
        subprocess.run(args, check=True)
        segments.append(segment)
        report.append({
            "shot": index, "asset_type": kind, "asset_path": str(source),
            "source_sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
            "duration_seconds": round(duration, 3),
            "renderer": "ffmpeg_media_asset",
        })

    concat_list = workdir / "visual_concat.txt"
    concat_list.write_text(
        "".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\n" for p in segments),
        encoding="utf-8",
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", str(output),
    ], check=True)
    return {
        "renderer": "ffmpeg_media_asset_timeline",
        "segments": report,
        "actual_assets_rendered": len(report),
    }


def render_review_copy(script_path: str | Path, approval_path: str | Path,
                       output_dir: str | Path) -> dict:
    script_path, approval_path = Path(script_path), Path(approval_path)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    scenes = validate_render_inputs(script, approval)
    outdir = Path(output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    build_dir = outdir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    timeline = []
    for index, scene in enumerate(scenes):
        timeline.append({
            "_start": index * 3.0,
            "_media": {"path": scene["asset_path"], "type": scene["asset_type"]},
            "visual_prompt": scene.get("image", ""),
            "on_screen_text": scene.get("label", ""),
            "source_refs": scene.get("source_ids", []),
        })
    licence = build_licence_register(timeline, outdir)
    if licence["blocked_count"]:
        raise ValueError(f"Licence register blocked {licence['blocked_count']} asset(s); replace them before rendering.")
    register_rows = __import__("csv").DictReader(
        open(licence["csv_path"], newline="", encoding="utf-8-sig")
    )
    statuses = [row["use_status"] for row in register_rows]
    if any(status not in {"eligible_pending_human_review", "ai_reconstruction_disclosure_and_terms_review"} for status in statuses):
        raise ValueError("An asset did not pass the conservative licence metadata gate.")
    # Metadata checks do not prove visual relevance or legal rights. Human review remains mandatory.

    from core.voice import synthesise
    wavs, durations = [], []
    for index, scene in enumerate(scenes):
        phrase_dir = build_dir / f"voice_{index:02d}"
        wav, _ = synthesise(scene["say"], phrase_dir,
                            language=script.get("language", "hindi"),
                            gender=script.get("voice_gender", "female"))
        wavs.append(wav)
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(wav)
        ], text=True).strip()
        durations.append(float(probe))

    voice_path = outdir / "voice.wav"
    inputs = []
    filters = []
    for index, wav in enumerate(wavs):
        inputs += ["-i", str(wav)]
        filters.append(f"[{index}:a]aresample=44100,aformat=sample_fmts=s16:channel_layouts=mono"
                       + (",apad=pad_dur=0.25" if index < len(wavs)-1 else "")
                       + f"[a{index}]")
    filters.append("".join(f"[a{i}]" for i in range(len(wavs)))
                   + f"concat=n={len(wavs)}:v=0:a=1[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filters), "-map", "[out]",
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(voice_path)],
                   check=True)
    total = sum(durations) + 0.25 * (len(durations)-1)
    ass_path = outdir / "captions.ass"
    _write_ass(scenes, durations, ass_path)

    # Render the actual downloaded/approved media assets. Do not substitute
    # generic motion-graphics cards for footage or photographs.
    visual_path = build_dir / "media_visuals.mp4"
    visual_report = _render_media_timeline(scenes, durations, visual_path, build_dir)
    final_path = outdir / "review_short.mp4"
    subtitle_filter = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    mux_duration = total + 0.6
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(visual_path),
        "-i", str(voice_path), "-vf", f"subtitles={subtitle_filter}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", f"aresample=44100,apad,atrim=duration={mux_duration:.3f},"
               f"afade=t=out:st={max(mux_duration-1.2, 0):.3f}:d=1.2",
        "-t", f"{mux_duration:.3f}", "-c:v", "libx264", "-preset", "fast",
        "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(final_path)
    ], check=True)
    shot_manifest = []
    shot_cursor = 0.0
    for index, (scene, duration) in enumerate(zip(scenes, durations), start=1):
        shot_manifest.append({
            "shot_id": f"shot_{index:02d}",
            "start_seconds": round(shot_cursor, 3),
            "duration_seconds": round(duration, 3),
            "narration": str(scene.get("say", "")),
            "visual_prompt": str(scene.get("image") or scene.get("visual_prompt") or ""),
            "visual_type": str(scene.get("asset_type") or "unknown"),
            "caption": str(scene.get("caption", "")),
            "source_ids": scene.get("source_ids", []),
        })
        shot_cursor += duration + (0.25 if index < len(scenes) else 0.0)

    manifest = {
        "status": "rendered_for_human_review",
        "video_path": str(final_path),
        "shots": shot_manifest,
        "duration_seconds": round(total + 0.6, 2),
        "resolution": "1080x1920",
        "fps": 30,
        "visual_renderer": visual_report,
        "visual_style": "actual_licensed_media_and_approved_stills_with_subtle_motion",
        "voice": str(voice_path),
        "captions": str(ass_path),
        "licence_register": licence["csv_path"],
        "credits": licence["credits_path"],
        "human_visual_rights_fact_and_audio_review_required": True,
        "upload_performed": False,
        "publishable": False,
    }
    (outdir / "production_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
