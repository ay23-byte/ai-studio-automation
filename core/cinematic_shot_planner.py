"""Free, provider-independent cinematic shot planning.

Adapted from the camera-control workflow in Open-Higgsfield-AI. This module only
builds prompts and manifests; it never calls MuAPI, Replicate, or any hosted
inference endpoint. Rendering/generation must be supplied by an explicitly
configured local backend or by rights-cleared source footage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CAMERAS = {
    "full_frame_cinema": "full-frame digital cinema camera",
    "super_35": "Super 35 digital cinema camera",
    "70mm_film": "large-format 70mm film camera",
    "16mm_film": "16mm documentary film camera",
    "large_format_digital": "large-format digital cinema camera",
}
LENSES = {
    "anamorphic": "anamorphic cinema lens with controlled oval bokeh",
    "vintage_prime": "vintage cinema prime lens with restrained halation",
    "modern_prime": "modern cinema prime lens with natural rendering",
    "macro": "macro cinema lens with precise close-focus detail",
    "tilt_shift": "tilt-shift lens with deliberate plane of focus",
    "wide_angle": "wide-angle cinema lens with natural perspective",
}
FOCAL_LENGTHS = {8, 14, 24, 28, 35, 50, 65, 85, 100, 135}
APERTURES = {"f/1.4": "very shallow depth of field", "f/2.8": "shallow depth of field",
             "f/4": "balanced depth of field", "f/8": "deep focus", "f/11": "deep focus"}
MOVEMENTS = {
    "locked": "locked-off camera, stable composition",
    "slow_push_in": "slow controlled dolly push-in",
    "slow_pull_out": "slow controlled dolly pull-out",
    "tracking": "smooth lateral tracking movement",
    "pan": "slow deliberate pan",
    "tilt": "slow deliberate tilt",
    "crane": "measured cinematic crane movement",
    "handheld": "restrained documentary handheld movement",
    "aerial": "smooth aerial establishing movement",
}
SHOT_SIZES = {
    "extreme_wide": "extreme wide establishing shot",
    "wide": "wide shot",
    "medium": "medium shot",
    "close_up": "close-up",
    "extreme_close_up": "extreme close-up detail",
    "overhead": "overhead top-down shot",
}
LIGHTING = {
    "natural": "motivated natural light and realistic shadows",
    "golden_hour": "soft golden-hour light",
    "overcast": "soft overcast documentary lighting",
    "low_key": "controlled low-key cinematic lighting",
    "studio": "soft controlled studio lighting",
}


def _choice(value: str, choices: dict[str, str], fallback: str) -> str:
    key = str(value or "").strip().lower()
    return choices.get(key, fallback)


def _movement_hint(value: str) -> str:
    """Normalize storyboard movement hints to renderer controls."""
    hint = str(value or "").strip().lower().replace("-", "_")
    if "locked" in hint or "static" in hint: return "locked"
    if "pull" in hint or "zoom_out" in hint: return "slow_pull_out"
    if "track" in hint or "lateral" in hint or "sideways" in hint: return "tracking"
    if "crane" in hint or "rise" in hint or "descend" in hint: return "crane"
    if "tilt" in hint or "vertical" in hint: return "tilt"
    if "pan" in hint or "sweep" in hint: return "pan"
    if "handheld" in hint: return "handheld"
    if "aerial" in hint or "drone" in hint: return "aerial"
    return "slow_push_in"

def build_cinematic_prompt(
    subject: str,
    *,
    camera: str = "full_frame_cinema",
    lens: str = "modern_prime",
    focal_length: int = 35,
    aperture: str = "f/4",
    movement: str = "slow_push_in",
    shot_size: str = "wide",
    lighting: str = "natural",
    aspect_ratio: str = "16:9",
    visual_guardrail: str = "",
) -> str:
    """Build a grounded cinematic prompt from shot intent and camera controls."""
    subject = " ".join(str(subject or "").split())
    if not subject:
        raise ValueError("subject must not be empty")
    try:
        focal = int(focal_length)
    except (TypeError, ValueError):
        focal = 35
    if focal not in FOCAL_LENGTHS:
        focal = 35
    ratio = str(aspect_ratio or "16:9").strip()
    if ratio not in {"16:9", "9:16", "1:1", "4:3", "2.39:1"}:
        ratio = "16:9"
    parts = [
        subject,
        _choice(shot_size, SHOT_SIZES, "wide shot"),
        _choice(movement, MOVEMENTS, MOVEMENTS["locked"]),
        f"shot on a {_choice(camera, CAMERAS, CAMERAS['full_frame_cinema'])}",
        f"{_choice(lens, LENSES, LENSES['modern_prime'])}, {focal}mm",
        f"{aperture if aperture in APERTURES else 'f/4'} ({APERTURES.get(aperture, APERTURES['f/4'])})",
        _choice(lighting, LIGHTING, LIGHTING["natural"]),
        f"{ratio} composition",
        "photorealistic documentary cinematography, physically plausible details, natural color grading",
    ]
    if visual_guardrail:
        parts.append("Visual constraints: " + " ".join(str(visual_guardrail).split()))
    parts.append(
        "Do not invent labels, readable text, logos, dates, archival markings, or historical evidence. "
        "Do not present generated imagery as authentic archival footage."
    )
    return ", ".join(parts)


def plan_cinematic_shots(
    shot_timeline: list[dict[str, Any]],
    *,
    defaults: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return copied shot records with reproducible cinema settings and prompts."""
    defaults = dict(defaults or {})
    planned: list[dict[str, Any]] = []
    for index, original in enumerate(shot_timeline, start=1):
        shot = dict(original)
        settings = dict(defaults)
        settings.update(shot.get("cinema_settings") or {})
        subject = (shot.get("visual_prompt") or shot.get("subject")
                   or shot.get("narration") or shot.get("title") or "")
        # Store the normalized value as well as using it in the prompt. The FFmpeg
        # renderer consumes cinema_settings directly and must receive a supported key.
        settings["movement"] = _movement_hint(
            settings.get("movement") or shot.get("camera_movement") or "slow_push_in"
        )
        prompt = build_cinematic_prompt(
            subject,
            camera=settings.get("camera", "full_frame_cinema"),
            lens=settings.get("lens", "modern_prime"),
            focal_length=settings.get("focal_length", 35),
            aperture=settings.get("aperture", "f/4"),
            movement=settings["movement"],
            shot_size=settings.get("shot_size", shot.get("shot_size", "wide")),
            lighting=settings.get("lighting", "natural"),
            aspect_ratio=settings.get("aspect_ratio", "16:9"),
            visual_guardrail=shot.get("visual_guardrail", ""),
        )
        shot["shot_id"] = str(shot.get("shot_id") or f"shot_{index:04d}")
        shot["cinema_settings"] = settings
        shot["cinematic_prompt"] = prompt
        shot["generation_backend"] = "not_configured"
        shot["generation_status"] = "prompt_only"
        planned.append(shot)
    return planned


def write_cinematic_shot_plan(
    shot_timeline: list[dict[str, Any]],
    output_path: str | Path,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a reviewable shot-plan JSON; no media is generated or uploaded."""
    shots = plan_cinematic_shots(shot_timeline, defaults=defaults)
    report = {
        "schema_version": 1,
        "shot_count": len(shots),
        "shots": shots,
        "generation_performed": False,
        "paid_provider_calls_performed": False,
        "human_review_required": True,
        "notice": (
            "This is a prompt and camera-settings plan only. It does not generate images/video, "
            "verify visual accuracy, grant asset rights, or approve publication. Configure a "
            "separately audited local backend or use rights-cleared footage."
        ),
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
