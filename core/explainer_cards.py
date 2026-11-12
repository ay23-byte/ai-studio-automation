"""Locally generated explainer cards: the offline-safe last-resort visual.

Root cause addressed: when every image/stock provider was unreachable (network
outage, rate limit, missing key) the whole build aborted after the script and
narration had already been produced. A designed, original graphic that carries
the scene's own keywords is not "unrelated filler", so it is a legitimate final
fallback. Every card is disclosed in a ``.source.json`` sidecar and counted by
``visual_fallback_report.json``; quality control downgrades a video that leans
on cards too heavily, and scheduled uploads stop for review in that case.

Cards are project-owned original graphics: no third-party licence is involved.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from pathlib import Path

PROVIDER = "local_explainer_card"
GRAPHIC_PROVIDER = "LabLens/AI Studio local graphic renderer"

_PALETTES = {
    "physics": ((8, 14, 34), (22, 44, 92), (92, 200, 255), (255, 196, 92)),
    "science": ((6, 20, 28), (14, 62, 72), (96, 226, 190), (255, 214, 120)),
    "technology": ((10, 12, 26), (36, 30, 84), (150, 130, 255), (96, 226, 220)),
    "engineering": ((12, 16, 24), (40, 52, 70), (255, 170, 70), (120, 200, 255)),
    "finance": ((8, 16, 30), (16, 44, 70), (232, 190, 110), (80, 200, 170)),
    "history": ((22, 16, 12), (66, 46, 30), (226, 188, 130), (200, 120, 90)),
    "news": ((14, 10, 16), (60, 18, 30), (255, 110, 110), (240, 240, 240)),
    "general": ((10, 14, 24), (30, 42, 70), (120, 190, 255), (255, 200, 110)),
}


def enabled() -> bool:
    return os.getenv("VISUAL_FALLBACK_CARDS", "1").strip().lower() in {"1", "true", "yes", "on"}


def max_share(default: float) -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("VISUAL_FALLBACK_MAX_SHARE", str(default)))))
    except ValueError:
        return default


def _keywords(text, limit=4):
    from core.dedupe import tokens
    seen, result = set(), []
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9+\-]*|[\u0900-\u097F]+", str(text or "")):
        key = word.lower()
        if key in seen or not tokens(word):
            continue
        seen.add(key)
        result.append(word)
        if len(result) >= limit:
            break
    return result


def _can_draw(text) -> bool:
    """Pillow shapes Devanagari correctly only with libraqm; never draw mojibake."""
    from core.fonts import contains_devanagari, devanagari_support
    if not contains_devanagari(text):
        return True
    support = devanagari_support()
    return bool(support.get("devanagari_font")) and bool(support.get("pillow_complex_shaping"))


def _gradient(size, top, bottom):
    from PIL import Image
    width, height = size
    strip = Image.new("RGB", (1, 256))
    strip.putdata([tuple(int(top[c] + (bottom[c] - top[c]) * i / 255) for c in range(3))
                   for i in range(256)])
    return strip.resize((width, height))


def _motif(draw, rng, box, accent, second, kind):
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    radius = min(right - left, bottom - top) / 2
    if kind == 0:  # orbit / atom
        for index in range(4):
            r = radius * (0.35 + 0.2 * index)
            draw.ellipse((cx - r, cy - r * 0.55, cx + r, cy + r * 0.55), outline=accent + (150,), width=5)
            angle = rng.uniform(0, math.tau)
            px, py = cx + r * math.cos(angle), cy + r * 0.55 * math.sin(angle)
            draw.ellipse((px - 16, py - 16, px + 16, py + 16), fill=second + (255,))
        draw.ellipse((cx - 46, cy - 46, cx + 46, cy + 46), fill=accent + (255,))
    elif kind == 1:  # waveform
        phase = rng.uniform(0, math.tau)
        for row, colour in enumerate((accent, second)):
            points = []
            for step in range(121):
                x = left + (right - left) * step / 120
                envelope = math.sin(math.pi * step / 120)
                y = cy + (row * 2 - 1) * 30 + envelope * radius * 0.42 * math.sin(
                    phase + step / (7.5 + row * 3.5))
                points.append((x, y))
            draw.line(points, fill=colour + (230,), width=7, joint="curve")
    elif kind == 2:  # network / graph
        nodes = [(rng.uniform(left + 40, right - 40), rng.uniform(top + 40, bottom - 40))
                 for _ in range(9)]
        for i, a in enumerate(nodes):
            for b in sorted(nodes, key=lambda n: (n[0] - a[0]) ** 2 + (n[1] - a[1]) ** 2)[1:3]:
                draw.line((a, b), fill=accent + (120,), width=4)
        for i, (x, y) in enumerate(nodes):
            r = 14 + (i % 3) * 7
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(second if i % 3 == 0 else accent) + (255,))
    else:  # rising bars
        bars = 7
        slot = (right - left) / bars
        base_y = bottom - 30
        heights = sorted(rng.uniform(0.2, 1.0) for _ in range(bars))
        for i, h in enumerate(heights):
            x0 = left + i * slot + slot * 0.2
            x1 = left + (i + 1) * slot - slot * 0.2
            y0 = base_y - h * (bottom - top - 80)
            draw.rounded_rectangle((x0, y0, x1, base_y), radius=14,
                                   fill=(second if i == bars - 1 else accent) + (235,))
        draw.line((left, base_y, right, base_y), fill=(255, 255, 255, 120), width=3)


_PLACEHOLDER_KEYWORDS = {
    "documentary subject", "subject-specific documentary scene", "documentary", "subject",
    "scene", "general", "topic", "b-roll", "broll", "stock", "footage", "cinematic",
}


def render_card(path, text, *, size=(1080, 1920), kicker="", index=0, total=1,
                category="general", keywords=None, channel="", headline=True) -> Path:
    """Write one original explainer still for a scene and its provenance sidecar."""
    from PIL import Image, ImageDraw
    from core.fonts import load_font

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    portrait = height >= width
    seed = int(hashlib.sha256(f"{text}|{index}".encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    top_c, bottom_c, accent, second = _PALETTES.get(str(category or "").lower(), _PALETTES["general"])
    image = _gradient(size, top_c, bottom_c).convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Soft background geometry for depth.
    for _ in range(5):
        r = rng.uniform(0.18, 0.5) * min(width, height)
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        draw.ellipse((x - r, y - r, x + r, y + r), outline=accent + (28,), width=3)
    step = max(width, height) // 14
    for x in range(0, width, step):
        draw.line((x, 0, x, height), fill=(255, 255, 255, 9), width=1)
    for y in range(0, height, step):
        draw.line((0, y, width, y), fill=(255, 255, 255, 9), width=1)

    # Captions occupy the lower-middle of a Short and the bottom of 16:9; keep
    # the motif and text inside the area burned-in subtitles do not use.
    if portrait:
        motif_box = (width * 0.14, height * 0.385, width * 0.86, height * 0.60)
        text_top, margin = int(height * 0.262), int(width * 0.09)
    else:
        motif_box = (width * 0.56, height * 0.2, width * 0.94, height * 0.74)
        text_top, margin = int(height * 0.26), int(width * 0.07)
    _motif(draw, rng, motif_box, accent, second, (seed + index) % 4)

    # Storyboard placeholders ("documentary subject") must never reach the screen.
    words = [w for w in (keywords or [])
             if str(w).strip() and str(w).strip().lower() not in _PLACEHOLDER_KEYWORDS] or _keywords(text)
    words = [str(w) for w in words if _can_draw(w)][:2 if portrait else 3]
    # The opening hook is burned in over the first portrait scene; stay clear of it.
    if headline and words and not (portrait and index == 0):
        scale = width / 1080 if portrait else width / 1920
        max_width = (width - 2 * margin) if portrait else int(width * 0.46)
        y = text_top
        if kicker and _can_draw(kicker):
            kicker_font = load_font(int(34 * scale) if portrait else int(30 * scale), True, kicker)
            draw.text((margin, y), str(kicker).upper()[:40], font=kicker_font, fill=second + (255,))
            y += int(58 * scale)
        for word in words:
            font_size = int((74 if portrait else 84) * scale)
            font = load_font(font_size, True, word)
            label = str(word)[:22].upper() if word.isascii() else str(word)[:22]
            while font_size > 30 and draw.textlength(label, font=font) > max_width:
                font_size -= 6
                font = load_font(font_size, True, word)
            draw.text((margin, y), label, font=font, fill=(245, 246, 250, 255))
            y += int(font_size * 1.16)
        draw.rounded_rectangle((margin, y + 10, margin + int(150 * scale), y + 22), radius=6,
                               fill=accent + (255,))

    # Progress pips: where this scene sits in the video.
    total = max(int(total), 1)
    if total > 1:
        pip_w = int(width * (0.045 if portrait else 0.03))
        gap = int(pip_w * 0.4)
        start_x = (width - (total * pip_w + (total - 1) * gap)) // 2
        y = int(height * (0.238 if portrait else 0.1))
        for i in range(min(total, 16)):
            fill = accent + (255,) if i == index else (255, 255, 255, 60)
            draw.rounded_rectangle((start_x + i * (pip_w + gap), y,
                                    start_x + i * (pip_w + gap) + pip_w, y + 8), radius=4, fill=fill)

    image = Image.alpha_composite(image, overlay).convert("RGB")
    image.save(path, quality=93)
    sidecar = path.with_suffix(path.suffix + ".source.json")
    sidecar.write_text(json.dumps({
        # These fields match the project-graphic contract in core.asset_licensing.
        "provider": GRAPHIC_PROVIDER,
        "asset_type": "programmatic_graphic",
        "ai_generated": False,
        "fallback": True,
        "fallback_kind": PROVIDER,
        "licence": "Project-created original graphic",
        "font_licence": "SIL Open Font License 1.1 (Noto Sans / Noto Sans Devanagari)",
        "font_licence_url": "https://openfontlicense.org",
        "font_attribution": "Typeface: Noto Sans, © The Noto Project Authors, SIL OFL 1.1.",
        "attribution_required": False,
        "reason": "No scene-matched image or footage provider was available for this scene.",
        "source_text": str(text or "")[:300],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def is_card(path) -> bool:
    sidecar = Path(str(path) + ".source.json")
    if not sidecar.is_file():
        return False
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")).get("fallback_kind") == PROVIDER
    except (OSError, ValueError):
        return False


def write_fallback_report(build_dir, media_paths, limit_share: float) -> dict:
    """Summarise how much of the video relies on fallback cards."""
    paths = [Path(p) for p in media_paths if p]
    cards = [p for p in paths if is_card(p)]
    share = (len(cards) / len(paths)) if paths else 0.0
    report = {
        "visual_count": len(paths),
        "fallback_card_count": len(cards),
        "fallback_card_share": round(share, 4),
        "max_share_allowed": limit_share,
        "status": "PASS" if not cards else ("REVIEW" if share <= limit_share else "FAIL"),
        "fallback_cards": [p.name for p in cards],
        "notice": ("Fallback cards are original generated graphics used only because no "
                   "scene-matched image or footage provider responded. Replace them with "
                   "relevant visuals before publishing where possible."),
    }
    Path(build_dir).mkdir(parents=True, exist_ok=True)
    (Path(build_dir) / "visual_fallback_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
