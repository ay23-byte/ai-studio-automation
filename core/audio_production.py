"""Shared, conservative audio treatment for every video production path.

Music generated here is an original synthesised instrumental bed; no third-party
recording is silently substituted. Supplied/licensed music always takes priority.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.ffmpeg_utils import ffmpeg_bin, render_timeout


def narration_cleanup_filter() -> str:
    """Gentle broadband cleanup; avoid aggressive gates that clip breaths/word tails."""
    return "aresample=48000,highpass=f=75,lowpass=f=14500,afftdn=nf=-28:tn=1"


def music_profile(topic: str = "", pipeline: str = "shorts") -> dict:
    text = str(topic or "").casefold()
    if any(word in text for word in ("war", "conflict", "crime", "mystery", "disaster", "investigation", "history")):
        mood, notes, label = "investigative", (110.0, 130.81, 164.81, 196.0), "restrained investigative tension"
    elif any(word in text for word in ("science", "physics", "space", "technology", "ai", "quantum", "future")):
        mood, notes, label = "discovery", (130.81, 164.81, 196.0, 261.63), "curious modern discovery"
    elif any(word in text for word in ("market", "economy", "business", "finance", "election", "policy")):
        mood, notes, label = "newsroom", (146.83, 174.61, 220.0, 293.66), "measured newsroom pulse"
    elif pipeline == "documentary":
        mood, notes, label = "cinematic", (98.0, 146.83, 196.0, 246.94), "cinematic atmospheric underscore"
    elif pipeline == "news":
        mood, notes, label = "newsroom", (146.83, 174.61, 220.0, 293.66), "measured newsroom pulse"
    else:
        mood, notes, label = "energetic", (164.81, 196.0, 246.94, 329.63), "upbeat explainer underscore"
    return {"mood": mood, "notes_hz": list(notes), "description": label,
            "pipeline_style": {"shorts": "high-energy cinematic explainer",
                               "news": "modern newsroom visual journalism",
                               "documentary": "cinematic investigative documentary"}.get(pipeline, pipeline),
            "visual_identity": {
                "shorts": {"palette": "deep navy, electric cyan, warm white", "type": "bold Noto Sans",
                           "transitions": "punchy cuts, short directional wipes, restrained impact zooms",
                           "pacing": "fast visual changes; explainers and animated diagrams"},
                "news": {"palette": "newsroom navy, white, restrained amber", "type": "clean Noto Sans",
                         "transitions": "clean broadcast cuts and restrained wipes",
                         "pacing": "fact-led, readable headlines, source evidence and maps"},
                "documentary": {"palette": "cinematic charcoal, muted gold, natural footage tones",
                                "type": "editorial Noto Sans", "transitions": "deliberate dissolves and motivated cuts",
                                "pacing": "measured chapters, archival footage and explanatory animation"},
            }.get(pipeline, {})}


def create_original_music_bed(path: str | Path, duration: float, topic: str = "",
                              pipeline: str = "shorts") -> tuple[Path, dict]:
    """Create an original four-note instrumental pad; no third-party music is used."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(2.0, float(duration))
    profile = music_profile(topic, pipeline)
    notes = profile["notes_hz"]
    inputs, filters = [], []
    volumes = (0.050, 0.032, 0.022, 0.012)
    for index, (frequency, volume) in enumerate(zip(notes, volumes)):
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency={frequency}:sample_rate=48000:duration={duration:.3f}"]
        filters.append(
            f"[{index}:a]volume={volume},tremolo=f={0.10 + index * 0.025:.3f}:d=0.18,"
            f"lowpass=f={1800 + index * 450},aformat=sample_fmts=fltp:channel_layouts=stereo"
            f"[m{index}]"
        )
    filters.append("".join(f"[m{i}]" for i in range(len(notes))) +
                   f"amix=inputs={len(notes)}:normalize=0,"
                   "afade=t=in:st=0:d=2,"
                   f"afade=t=out:st={max(0.0, duration-3):.3f}:d={min(3.0,duration):.3f},"
                   "alimiter=limit=0.75[out]")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filters), "-map", "[out]",
                    "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", "-ar", "48000",
                    "-ac", "2", str(path)], check=True, timeout=render_timeout())
    if not path.is_file() or path.stat().st_size < 1000:
        raise RuntimeError("Original topic-matched instrumental bed could not be generated.")
    return path, profile


def write_audio_production_report(directory: str | Path, profile: dict, music_source: str,
                                  denoise_filter: str | None = None,
                                  mix_settings: dict | None = None) -> dict:
    directory = Path(directory)
    report = {
        "schema_version": 2,
        "status": "rendered_pending_measurement_and_human_listening_review",
        "narration_cleanup": {"enabled": True, "filter": denoise_filter or narration_cleanup_filter(),
                              "strength": "gentle", "sample_rate_hz": 48000},
        "music": {"source": music_source, "licensing": "original_synthesised_instrumental"
                  if music_source == "original_generated" else "caller_supplied_track_requires_licence_review",
                  **profile},
        "mix_policy": {"narration_priority": True, "sidechain_ducking": True,
                       "music_during_pauses": True, "human_listening_review_required": True,
                       "final_sample_rate_hz": 48000,
                       **(mix_settings or {})},
        "copyright_note": "Generated instrumental is original synthesis; supplied tracks still require documented usage rights.",
    }
    (directory / "audio_production_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
