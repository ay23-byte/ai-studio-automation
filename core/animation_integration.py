"""Strictly matched local Manim/Blender explainers shared by production pipelines.

These are original conceptual illustrations, never substitutes for archival
footage or evidence screenshots. Unsupported/ambiguous narration stays on the
normal footage path.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_NEGATION = re.compile(r"\b(?:not|never|without|neither|no)\b", re.I)
_ALLOWED_VISUALS = {"diagram", "animation", "map", "news_graphic"}


def select_animation_kind(narration: str, visual_type: str, visual_prompt: str = "") -> str | None:
    """Select only a positively stated, unambiguous concept requested as a diagram."""
    narration = str(narration or "").strip()
    kind = str(visual_type or "").strip().lower().replace("-", "_")
    prompt = str(visual_prompt or "").strip().lower()
    if kind not in _ALLOWED_VISUALS and not any(
        x in prompt for x in ("diagram", "animation", "scientific illustration")
    ):
        return None
    if not narration or _NEGATION.search(narration):
        return None

    concepts = (
        (r"\b(?:sinusoidal|sine) waves?\b", "manim_wave"),
        (r"\bprojectile motion\b", "manim_projectile"),
        (r"\bvector addition\b", "manim_vectors"),
        (r"\b(?:solar system|planets? orbit(?:ing)? the sun)\b", "blender_solar_system"),
        (r"\b(?:atom|atomic model|electrons? orbit(?:ing)? the nucleus)\b", "blender_atom"),
        (r"\b(?:earth[- ]moon system|moon orbit(?:s|ing)? (?:the )?earth)\b", "blender_earth_moon"),
        (r"\bcircular orbits?\b", "blender_orbit"),
        (r"\b(?:rotating earth|earth globe|global geography|geographic globe|planet earth)\b", "blender_earth_globe"),
        (r"\b(?:volcano eruption|volcanic eruption|magma chamber)\b", "blender_volcano"),
        (r"\b(?:satellite orbit|spacecraft orbit|satellite deployment)\b", "blender_satellite"),
        (r"\b(?:wind turbine|turbine rotor|turbine blades?)\b", "blender_turbine"),
        (r"\b(?:calculus|derivative of|integral of|tangent slope)\b", "manim_calculus"),
        (r"\b(?:electric field lines?|magnetic field lines?|electromagnetic field)\b", "manim_field_lines"),
        (r"\b(?:fourier series|fourier transform|harmonic decomposition)\b", "manim_fourier"),
        (r"\b(?:neural network|artificial neural network)\b", "manim_neural_network"),
    )
    matches = [name for pattern, name in concepts if re.search(pattern, narration, re.I)]
    return matches[0] if len(matches) == 1 else None


def render_animation(*, narration: str, visual_type: str, visual_prompt: str,
                     output_dir: str | Path, scene_id: str,
                     content_type: str = "physics_short") -> dict | None:
    """Render and probe one matched 4-second animation; never silently use filler."""
    if os.getenv("PV2_ANIMATION_MODE", "off").strip().lower() != "auto_strict":
        return None
    kind = str(visual_type or "").strip().lower().replace("-", "_")
    selected = select_animation_kind(narration, kind, visual_prompt)
    if not selected:
        return None

    from src.production_v2.config import V2Config
    from src.production_v2.ffmpeg_io import probe
    from src.production_v2.licenses import AssetLibrary
    from src.production_v2.models import Scene, VisualSpec
    from src.production_v2.visuals import plan_visuals

    root = Path(output_dir) / "scientific_animations"
    root.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(scene_id))[:64] or "scene"
    env = dict(os.environ)
    env.update({"PV2_ANIMATION_MODE": "auto_strict", "PV2_BLENDER_BACKEND": "bpy"})
    config = V2Config.from_env(env)
    scene = Scene(
        id=safe_id,
        narration=str(narration or "").strip(),
        visual=VisualSpec(
            description=(str(visual_prompt or narration)[:500] +
                         " — conceptual educational animation, not event footage"),
            kind="animation" if kind == "animation" else "diagram",
            duration=4.0,
            on_screen_text="CONCEPTUAL ILLUSTRATION",
        ),
    )
    assets = plan_visuals([scene], root, AssetLibrary(), config, content_type)
    if len(assets) != 1:
        raise RuntimeError("animation renderer did not return exactly one asset")
    asset = assets[0]
    path = Path(asset.image_path)
    if not path.is_file() or path.stat().st_size < 1000:
        raise RuntimeError("animation output is missing or too small")
    media = probe(path, config)
    streams = [s for s in media.get("streams", []) if s.get("codec_type") == "video"]
    rules = config.rules_for(content_type)
    if len(streams) != 1:
        raise RuntimeError("animation output must contain exactly one video stream")
    stream = streams[0]
    if (int(stream.get("width") or 0), int(stream.get("height") or 0)) != (rules.width, rules.height):
        raise RuntimeError("animation output resolution does not match the pipeline")
    duration = float((media.get("format") or {}).get("duration") or 0)
    if not 3.0 <= duration <= 5.0:
        raise RuntimeError(f"animation duration {duration:.2f}s is outside the 3–5s QA range")
    renderer = "manim" if selected.startswith("manim_") else "blender"
    provenance = {
        "provider": f"local-{renderer}", "asset_type": "scientific_animation",
        "renderer": renderer, "concept": selected.removeprefix("manim_").removeprefix("blender_"),
        "narration_anchor": str(narration)[:500], "visual_type": kind or "diagram",
        "ai_generated": False, "licence": "Project-created original conceptual animation",
        "automated_qa": {"status": "PASS", "resolution": f"{rules.width}x{rules.height}",
                         "duration_seconds": round(duration, 3), "video_stream": True},
        "approval_policy": "automatic_after_machine_qa",
    }
    path.with_suffix(path.suffix + ".source.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"type": "video", "path": path, "provider": f"local-{renderer}",
            "automated_qa": "PASS", "renderer": renderer}
