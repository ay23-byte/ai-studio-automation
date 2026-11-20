"""Optional, strictly matched Manim/Blender scenes shared by all legacy pipelines.

Only two reviewed-in-code conceptual animations are supported. A renderer is
never substituted for archival footage or an unrelated scene. Rendered clips
are independently probed before they can enter an edit.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


_NEGATION = re.compile(r"\b(?:not|never|without|neither|no)\b", re.I)
_WAVE = re.compile(r"\b(?:sinusoidal|sine) wave\b", re.I)
_ORBIT = re.compile(r"\bcircular orbit\b", re.I)
_ALLOWED_VISUALS = {"diagram", "animation", "map", "news_graphic"}


def render_animation(*, narration: str, visual_type: str, visual_prompt: str,
                     output_dir: str | Path, scene_id: str,
                     content_type: str = "physics_short") -> dict | None:
    """Return a verified animation media record, or None when the scene is ineligible.

    Eligibility is deliberately narrow: the narration must positively state one
    supported concept and the storyboard must request an explanatory visual.
    Missing renderers or failed renders raise; they never silently become filler.
    """
    if os.getenv("PV2_ANIMATION_MODE", "off").strip().lower() != "auto_strict":
        return None
    narration = str(narration or "").strip()
    kind = str(visual_type or "").strip().lower().replace("-", "_")
    prompt = str(visual_prompt or "").strip().lower()
    if kind not in _ALLOWED_VISUALS and not any(x in prompt for x in ("diagram", "animation", "scientific illustration")):
        return None
    wave, orbit = bool(_WAVE.search(narration)), bool(_ORBIT.search(narration))
    if wave == orbit or _NEGATION.search(narration):
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
        narration=narration,
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
    renderer = "manim" if wave else "blender"
    provenance = {
        "provider": f"local-{renderer}",
        "asset_type": "scientific_animation",
        "renderer": renderer,
        "concept": "sine_wave" if wave else "circular_orbit",
        "narration_anchor": narration[:500],
        "visual_type": kind or "diagram",
        "ai_generated": False,
        "licence": "Project-created original conceptual animation",
        "automated_qa": {"status": "PASS", "resolution": f"{rules.width}x{rules.height}",
                         "duration_seconds": round(duration, 3), "video_stream": True},
        "human_review": "not_required_for_technical_animation_gate",
    }
    path.with_suffix(path.suffix + ".source.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"type": "video", "path": path, "provider": f"local-{renderer}",
            "automated_qa": "PASS", "renderer": renderer}
