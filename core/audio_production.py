"""Shared audio production for Shorts, news Shorts and long-form documentaries.

All generated music is original synthesis. It is a restrained, topic-matched
underscore rather than a substitute for licensed commercial recordings.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.ffmpeg_utils import ffmpeg_bin, render_timeout


def narration_cleanup_filter() -> str:
    """Conservative voice cleanup; preserve consonants, breaths and word tails."""
    return "aresample=48000,highpass=f=75,lowpass=f=14500,afftdn=nf=-28:tn=1"


def music_profile(topic: str = "", pipeline: str = "shorts") -> dict:
    text = str(topic or "").casefold()
    if any(word in text for word in ("war", "conflict", "crime", "mystery", "disaster", "investigation", "history", "village")):
        mood, notes, bass, bpm, label = "investigative", (110.00, 130.81, 164.81, 196.00), 55.00, 76, "restrained investigative tension"
    elif any(word in text for word in ("science", "physics", "space", "technology", "ai", "quantum", "future", "discovery")):
        mood, notes, bass, bpm, label = "discovery", (130.81, 164.81, 196.00, 261.63), 65.41, 104, "curious modern discovery"
    elif any(word in text for word in ("market", "economy", "business", "finance", "election", "policy", "breaking", "news")):
        mood, notes, bass, bpm, label = "newsroom", (146.83, 174.61, 220.00, 293.66), 73.42, 112, "measured newsroom pulse"
    elif pipeline == "documentary":
        mood, notes, bass, bpm, label = "cinematic", (98.00, 146.83, 196.00, 246.94), 49.00, 68, "cinematic atmospheric underscore"
    elif pipeline == "news":
        mood, notes, bass, bpm, label = "newsroom", (146.83, 174.61, 220.00, 293.66), 73.42, 112, "measured newsroom pulse"
    else:
        mood, notes, bass, bpm, label = "energetic", (164.81, 196.00, 246.94, 329.63), 82.41, 120, "upbeat explainer underscore"
    return {
        "mood": mood, "notes_hz": list(notes), "bass_hz": bass, "tempo_bpm": bpm,
        "description": label,
        "arrangement": ["warm_harmonic_pad", "soft_low_pulse", "subtle_rhythm", "gentle_reverb"],
        "pipeline_style": {
            "shorts": "high-energy cinematic explainer",
            "news": "modern newsroom visual journalism",
            "documentary": "cinematic investigative documentary",
        }.get(pipeline, pipeline),
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
        }.get(pipeline, {}),
    }


def create_original_music_bed(path: str | Path, duration: float, topic: str = "",
                              pipeline: str = "shorts") -> tuple[Path, dict]:
    """Render a multi-layer original underscore with mood-specific harmony and pulse."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(2.0, float(duration))
    profile = music_profile(topic, pipeline)
    notes = profile["notes_hz"]
    frequencies = [*notes, float(profile["bass_hz"]), float(profile["bass_hz"]) * 2.0]
    # Keep the music stem deliberately quiet; narration is mixed separately and
    # sidechain-compresses this stem in every production renderer.
    volumes = (0.030, 0.021, 0.015, 0.009, 0.035, 0.010)
    inputs, filters = [], []
    for index, (frequency, volume) in enumerate(zip(frequencies, volumes)):
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency={frequency:.3f}:sample_rate=48000:duration={duration:.3f}"]
        if index < 4:
            treatment = f"tremolo=f={max(0.1, 0.12 + index * 0.02):.3f}:d=0.12,lowpass=f={1500 + index * 500}"
        elif index == 4:
            treatment = f"tremolo=f={profile['tempo_bpm'] / 120.0:.3f}:d=0.85,lowpass=f=180"
        else:
            treatment = f"tremolo=f={profile['tempo_bpm'] / 60.0:.3f}:d=0.9,lowpass=f=700"
        filters.append(
            f"[{index}:a]volume={volume},{treatment},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[m{index}]"
        )
    labels = "".join(f"[m{i}]" for i in range(len(frequencies)))
    filters.append(
        f"{labels}amix=inputs={len(frequencies)}:normalize=0,"
        "aecho=0.8:0.35:45|90:0.12|0.06,"
        "afade=t=in:st=0:d=1.5,"
        f"afade=t=out:st={max(0.0, duration - 2.5):.3f}:d={min(2.5, duration):.3f},"
        "alimiter=limit=0.70[out]"
    )
    subprocess.run(
        [ffmpeg_bin(), "-y", "-loglevel", "error", *inputs, "-filter_complex",
         ";".join(filters), "-map", "[out]", "-t", f"{duration:.3f}",
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(path)],
        check=True, timeout=render_timeout(),
    )
    if not path.is_file() or path.stat().st_size < 1000:
        raise RuntimeError("Original topic-matched instrumental bed could not be generated.")
    return path, profile


def write_audio_production_report(directory: str | Path, profile: dict, music_source: str,
                                  denoise_filter: str | None = None,
                                  mix_settings: dict | None = None) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 3,
        "status": "rendered_pending_measurement_and_human_listening_review",
        "narration_cleanup": {
            "enabled": True, "filter": denoise_filter or narration_cleanup_filter(),
            "strength": "gentle", "sample_rate_hz": 48000,
            "note": "Source narration must be checked before mixing; denoising cannot repair distorted TTS.",
        },
        "music": {
            "source": music_source,
            "licensing": "original_synthesised_instrumental" if music_source == "original_generated"
                         else "caller_supplied_track_requires_licence_review",
            **profile,
        },
        "mix_policy": {
            "narration_priority": True, "sidechain_ducking": True,
            "music_during_pauses": True, "music_base_level": "subtle",
            "voice_music_separation_listening_check": True,
            "human_listening_review_required": True,
            **(mix_settings or {}),
        },
        "copyright_note": "Generated instrumental is original synthesis; supplied tracks still require documented usage rights.",
    }
    (directory / "audio_production_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
