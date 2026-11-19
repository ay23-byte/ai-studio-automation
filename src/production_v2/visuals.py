"""Purpose-built illustrative visuals with provenance (no unrelated stock filler)."""
from __future__ import annotations

import datetime as dt
import math
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


def plan_visuals(scenes: list[Scene], store_root: Path, library: AssetLibrary,
                 config: V2Config, content_type: str) -> list[VisualAsset]:
    """Generate one purpose-built visual per scene and register its provenance."""
    rules = config.rules_for(content_type)
    assets: list[VisualAsset] = []
    for scene in scenes:
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
