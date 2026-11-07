"""Render human-approved news Shorts through the existing TTS and FFmpeg renderer.

This stage intentionally stops at a review MP4. It never uploads or publishes.
"""
from __future__ import annotations

import json
import os
import subprocess
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
        if not isinstance(metadata, dict) or not metadata.get("provider") or not (
            metadata.get("page_url") or metadata.get("url")
        ):
            raise ValueError(f"Scene {index} sidecar needs provider and page_url/url.")
        if str(metadata.get("asset_type", "")).lower().startswith("ai_generated"):
            if not scene.get("ai_disclosure") or not metadata.get("terms_reviewed"):
                raise ValueError(f"Scene {index} AI asset needs ai_disclosure=true and terms_reviewed=true.")
        else:
            if not metadata.get("licence") and not metadata.get("license"):
                raise ValueError(f"Scene {index} real asset needs an explicit licence label.")
            if not metadata.get("licence_url") and not metadata.get("license_url"):
                raise ValueError(f"Scene {index} real asset needs a licence URL.")
    return scenes


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass(scenes: list[dict], durations: list[float], output: Path) -> None:
    rows = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: News,Noto Sans Devanagari,72,&H00FFFFFF,&H0000D7FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,650,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    cursor = 0.0
    for scene, duration in zip(scenes, durations):
        caption = str(scene["caption"]).replace("\\N", " ").replace("\n", " ").replace("{", "").replace("}", "")
        rows.append(f"Dialogue: 0,{_ass_time(cursor)},{_ass_time(cursor + duration)},News,,0,0,0,,{caption}")
        cursor += duration + 0.25
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")


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

    from core.editor_shorts import render
    media = [{"path": scene["asset_path"], "type": scene["asset_type"]} for scene in scenes]
    cuts, cursor = [], 0.0
    for duration in durations[:-1]:
        cursor += duration + 0.25
        cuts.append(cursor)
    final_path = outdir / "review_short.mp4"
    render(media, durations, cuts, voice_path, ass_path, total, final_path,
           music=None, sfx_vol=0.0)
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
