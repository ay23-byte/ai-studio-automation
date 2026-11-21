"""Purpose-built illustrative visuals with provenance (no unrelated stock filler)."""
from __future__ import annotations

import datetime as dt
import math
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from .artifacts import sha256_file
from .config import ROOT, ContentRules, V2Config
from .licenses import AssetLibrary, AssetRecord
from .models import Scene

FONT_PATH = ROOT / "assets" / "fonts" / "NotoSans-Regular.ttf"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:6]


@dataclass
class VisualAsset:
    scene_id: str
    image_path: Path
    kind: str
    description: str
    record: AssetRecord


class FootageProvider(Protocol):
    """Future free archive adapters implement this; no paid provider is accepted."""

    def find(self, scene: Scene) -> AssetRecord | None: ...


class SyntheticVisualGenerator:
    """Draws distinct, labelled explanatory frames for offline demonstration renders."""

    PALETTE = {
        "news_graphic": ((12, 21, 38), (55, 155, 255)),
        "animation": ((9, 24, 32), (72, 220, 180)),
        "diagram": ((12, 21, 38), (255, 196, 84)),
        "map": ((16, 27, 42), (120, 200, 255)),
        "archive": ((35, 30, 24), (204, 168, 110)),
        "illustrative": ((20, 16, 36), (200, 150, 255)),
        "generated": ((18, 22, 30), (190, 205, 225)),
    }

    def draw(self, scene: Scene, rules: ContentRules, output: Path) -> Path:
        background, accent = self.PALETTE.get(scene.visual.kind, self.PALETTE["generated"])
        image = Image.new("RGB", (rules.width, rules.height), background)
        draw = ImageDraw.Draw(image)
        w, h = image.size
        pad = int(min(w, h) * 0.07)

        # Distinct visual structures per content type; no random stock imagery.
        if scene.visual.kind in {"map", "news_graphic"}:
            for x in range(pad, w - pad, max(40, w // 16)):
                draw.line([(x, pad), (x, h - pad)], fill=tuple(c // 3 for c in accent), width=1)
            for y in range(pad, h - pad, max(40, h // 16)):
                draw.line([(pad, y), (w - pad, y)], fill=tuple(c // 3 for c in accent), width=1)
            draw.ellipse([w * 0.55, h * 0.20, w * 0.82, h * 0.55], outline=accent, width=8)
        elif scene.visual.kind in {"animation", "diagram"}:
            axis_y = int(h * 0.62)
            draw.line([(pad, axis_y), (w - pad, axis_y)], fill=accent, width=5)
            for i in range(1, 9):
                x = pad + int((w - 2 * pad) * i / 9)
                amplitude = math.sin(i / 2.0) * h * 0.12
                draw.line([(x, axis_y), (x, int(axis_y - amplitude))], fill=accent, width=6)
        else:
            draw.rectangle([pad, pad, w - pad, h // 3], outline=accent, width=6)
            draw.line([(pad, h // 3), (w - pad, h // 3)], fill=accent, width=4)

        label_font = _font(max(20, min(w, h) // 32))
        body_font = _font(max(18, min(w, h) // 42))
        small_font = _font(max(14, min(w, h) // 58))
        draw.text((pad, pad // 2), scene.visual.on_screen_text or scene.id.upper(),
                  font=label_font, fill=accent)
        for index, line in enumerate(_wrap(scene.visual.description, 46)):
            draw.text((pad, h // 3 + pad + index * int(min(w, h) * 0.055)), line,
                      font=body_font, fill=(240, 244, 250))
        disclaimer = ("ILLUSTRATIVE GRAPHIC — NOT ACTUAL EVENT FOOTAGE"
                      if scene.visual.kind in {"news_graphic", "illustrative", "generated", "archive"}
                      else "ANIMATED EXPLAINER — CONCEPTUAL DIAGRAM")
        draw.rectangle([pad, h - pad - 56, w - pad, h - pad + 8], fill=(0, 0, 0))
        draw.text((pad + 8, h - pad - 48), disclaimer, font=small_font, fill=(255, 220, 150))

        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        return output



def choose_visuals(scenes: list[Scene], config: V2Config) -> dict[str, dict]:
    """Conservative draft selection; automatic choices are limited to explicit concepts and final-video machine QA.

    A keyword match cannot establish scientific correctness. Only the small,
    explicit concept library is auto-selected; ambiguity and unsupported claim
    scenes fail closed rather than using unrelated footage.
    """
    choices = {}
    if config.visual_manifest:
        choices = json.loads(Path(config.visual_manifest).read_text(encoding="utf-8"))
        if not isinstance(choices, dict) or set(choices) - {scene.id for scene in scenes}:
            raise ValueError("visual manifest must map known scene IDs to choices")
        for choice in choices.values():
            if not isinstance(choice, dict) or choice.get("reviewed") is not True or "automatic" in choice:
                raise ValueError("visual manifest choices require explicit review")
    if config.animation_mode not in {"off", "auto_strict"}:
        raise ValueError("animation mode must be off or auto_strict")
    if config.animation_mode == "auto_strict":
        for scene in scenes:
            if scene.id in choices or scene.visual.kind not in {"animation", "diagram", "map", "news_graphic"}:
                continue
            # Reuse the same conservative matcher as the legacy Shorts/documentary
            # renderer so both production stacks make identical scene choices.
            from core.animation_integration import select_animation_kind
            selected = select_animation_kind(scene.narration, scene.visual.kind, scene.visual.description)
            if selected:
                choices[scene.id] = {"type": selected, "automatic": True}
            else:
                raise ValueError(f"scene {scene.id}: unsupported or ambiguous scientific animation; provide reviewed visual")
    return choices


def plan_visuals(scenes: list[Scene], store_root: Path, library: AssetLibrary,
                 config: V2Config, content_type: str) -> list[VisualAsset]:
    """Generate visuals with explicit provenance and review status."""
    rules = config.rules_for(content_type)
    assets: list[VisualAsset] = []
    choices = choose_visuals(scenes, config)
    for scene in scenes:
        choice = choices.get(scene.id)
        if choice is not None:
            if not isinstance(choice, dict) or (choice.get("reviewed") is not True and not choice.get("automatic")):
                raise ValueError(f"scene {scene.id} requires explicit visual review")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", scene.id):
                raise ValueError("unsafe scene ID")
            if choice.get("type") == "commons":
                from .archive import download_commons
                image, record = download_commons(choice.get("title", ""), store_root / f"visual_{scene.id}", scene.id,
                                                 timeout=config.request_timeout_seconds)
            elif str(choice.get("type", "")).startswith("manim_"):
                if scene.visual.kind not in {"animation", "diagram", "map", "news_graphic"}:
                    raise ValueError("Manim animation is only allowed for explanatory visual scenes")
                from .ffmpeg_io import run
                scene_names = {
                    "manim_wave": "WaveIllustration",
                    "manim_projectile": "ProjectileMotion",
                    "manim_vectors": "VectorAddition",
                }
                scene_type = choice["type"]
                if scene_type not in scene_names:
                    raise ValueError(f"unsupported Manim scene: {scene_type}")
                class_name = scene_names[scene_type]
                media_dir = Path(tempfile.mkdtemp(prefix=f"manim_{scene.id}_", dir=store_root))
                run([sys.executable, "-m", "manim", "render", "--disable_caching", "--media_dir", str(media_dir),
                     "-r", f"{rules.width},{rules.height}", "--fps", str(rules.fps),
                     str(Path(__file__).with_name("manim_wave.py")), class_name],
                    timeout=config.ffmpeg_timeout_seconds)
                candidates = list(media_dir.rglob(f"{class_name}.mp4"))
                if len(candidates) != 1 or candidates[0].stat().st_size == 0:
                    raise ValueError(f"Manim did not produce the expected {class_name} video")
                image = candidates[0]
                record = AssetRecord(id=f"visual_{scene.id}", source_url=f"generated-locally://{scene_type}",
                    creator="Engineering Physics v2", license="Project-created conceptual animation",
                    downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), sha256=sha256_file(image),
                    media_type="video", resolution=f"{rules.width}x{rules.height}", synthetic=True,
                    used_in=scene.id, restrictions="Conceptual Manim explainer; not documentary event footage.")
            elif str(choice.get("type", "")).startswith("blender_"):
                if scene.visual.kind not in {"animation", "diagram", "map", "news_graphic"}:
                    raise ValueError("orbit animation is only allowed for claim-bearing visual scenes")
                from .ffmpeg_io import resolve_ffmpeg, run
                ffmpeg = resolve_ffmpeg(config)
                if not ffmpeg:
                    raise ValueError("FFmpeg is required to encode Blender frames")
                # Use an isolated directory so a previous render cannot be mistaken for success.
                scene_type = choice["type"]
                if scene_type not in {"blender_orbit", "blender_solar_system", "blender_atom", "blender_earth_moon"}:
                    raise ValueError(f"unsupported Blender scene: {scene_type}")
                media_dir = Path(tempfile.mkdtemp(prefix=f"blender_{scene.id}_", dir=store_root))
                frames = media_dir / "frame_"
                script = str(Path(__file__).with_name("blender_orbit.py"))
                if config.blender_backend == "blender":
                    command = [config.blender_binary, "--background", "--factory-startup", "--python", script]
                elif config.blender_backend == "bpy":
                    command = [sys.executable, script]
                else:
                    raise ValueError("unsupported Blender backend")
                run([*command, "--", "--output", str(frames), "--width", str(rules.width),
                     "--height", str(rules.height), "--fps", str(rules.fps), "--scene-kind", scene_type],
                    timeout=config.ffmpeg_timeout_seconds)
                expected = rules.fps * 4
                if any(not (media_dir / f"frame_{index:04d}.png").is_file()
                       for index in range(1, expected + 1)):
                    raise ValueError("Blender did not produce every expected animation frame")
                image = media_dir / "orbit.mp4"
                run([ffmpeg, "-y", "-hide_banner", "-loglevel", "warning", "-framerate", str(rules.fps),
                     "-start_number", "1", "-i", str(media_dir / "frame_%04d.png"), "-frames:v", str(expected),
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(image)],
                    timeout=config.ffmpeg_timeout_seconds)
                if not image.is_file() or image.stat().st_size == 0:
                    raise ValueError("Blender orbit video encoding failed")
                for index in range(1, expected + 1):
                    (media_dir / f"frame_{index:04d}.png").unlink()
                record = AssetRecord(id=f"visual_{scene.id}", source_url="generated-locally://blender-orbit",
                    creator="Engineering Physics v2", license="synthetic demonstration graphic",
                    downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), sha256=sha256_file(image),
                    media_type="video", resolution=f"{rules.width}x{rules.height}", synthetic=True,
                    used_in=scene.id, restrictions=f"{scene_type} is a conceptual 3D illustration; sizes and distances are not to scale; not event footage; verify relevance and accuracy.")
            else:
                raise ValueError(f"unsupported visual type for {scene.id}")
            if choice.get("automatic"):
                record.restrictions += "; automatic conceptual illustration; final render must pass technical and motion QA"
            library.add(record)
            assets.append(VisualAsset(scene.id, image, scene.visual.kind, scene.visual.description, record))
            continue
        image = SyntheticVisualGenerator().draw(scene, rules,
                                                store_root / f"visual_{scene.id}.png")
        record = AssetRecord(
            id=f"visual_{scene.id}", source_url="generated-locally://production_v2",
            creator="Engineering Physics v2 synthetic visual generator",
            license="synthetic demonstration graphic", attribution_text="",
            downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            sha256=sha256_file(image), media_type="image",
            resolution=f"{rules.width}x{rules.height}", synthetic=True,
            used_in=scene.id, restrictions="illustrative only; not documentary evidence")
        library.add(record)
        assets.append(VisualAsset(scene.id, image, scene.visual.kind,
                                  scene.visual.description, record))
    return assets
