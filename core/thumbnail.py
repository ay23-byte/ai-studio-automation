"""Create several readable 1280x720 YouTube thumbnail options."""
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

SIZE = (1280, 720)


def _font(size, bold=True):
    candidates = (
        ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/Arial.ttf"]
        if bold else ["C:/Windows/Fonts/arial.ttf"]
    ) + (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _source_frame(source: Path, work: Path):
    source = Path(source)
    if source.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
        frame = work / "thumbnail_source.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
             "-i", str(source), "-frames:v", "1", str(frame)],
            check=True,
        )
        return frame
    return source


def _wrap(draw, text, font, max_width):
    words, lines, current = str(text or "New Video").split(), [], ""
    for word in words:
        test = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), test, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)
    return lines[:3]


def _render(base, title, channel, out, style):
    image = base.copy().convert("RGB")
    image = ImageEnhance.Color(image).enhance(1.18)
    image = ImageEnhance.Contrast(image).enhance(1.12)
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if style == "bold":
        for x in range(0, 760):
            alpha = int(205 * (1 - x / 760))
            draw.line((x, 0, x, 720), fill=(4, 9, 25, alpha))
        color, accent, y0 = (255, 255, 255, 255), (255, 205, 45, 255), 300
    elif style == "minimal":
        draw.rounded_rectangle((54, 430, 1226, 666), radius=28, fill=(5, 10, 20, 190))
        color, accent, y0 = (255, 255, 255, 255), (100, 225, 205, 255), 470
    else:
        draw.rectangle((0, 0, 1280, 720), fill=(5, 10, 28, 55))
        draw.rounded_rectangle((55, 400, 1225, 670), radius=22, fill=(5, 12, 30, 205))
        color, accent, y0 = (255, 255, 255, 255), (255, 205, 45, 255), 435
    font = _font(76 if style != "minimal" else 62)
    lines = _wrap(draw, title, font, 1080)
    line_height = 88 if style != "minimal" else 76
    for i, line in enumerate(lines):
        y = y0 + i * line_height
        draw.text((84, y), line, font=font, fill=color, stroke_width=3, stroke_fill=(0, 0, 0, 220))
    small = _font(30, False)
    draw.text((84, 48), channel[:48], font=small, fill=accent)
    image = Image.alpha_composite(image.convert("RGBA"), overlay)
    image.convert("RGB").save(out, "JPEG", quality=94, optimize=True)
    return Path(out)


def generate_variants(source_path, title, output_dir, channel="AI Studio"):
    """Return three thumbnail file paths: bold, cinematic and minimal."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = _source_frame(Path(source_path), output_dir)
    if not source.exists():
        raise FileNotFoundError(f"Thumbnail source not found: {source}")
    with Image.open(source) as opened:
        base = ImageOps.fit(opened.convert("RGB"), SIZE, method=Image.Resampling.LANCZOS)
    return [
        _render(base, title, channel, output_dir / f"thumbnail_{style}.jpg", style)
        for style in ("bold", "cinematic", "minimal")
    ]


def generate_topic_variants(title, script, output_dir, channel="AI Studio"):
    """Generate three topic-based AI backgrounds and compose readable thumbnails.

    Does not require a rendered or downloaded MP4. Uses the project's configured
    image provider and falls back to a clean graphic background if unavailable.
    """
    from core.images import generate_image

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes") or script.get("sections") or []
    visual_hint = ""
    if scenes:
        visual_hint = str(scenes[0].get("image") or scenes[0].get("title") or "")[:300]
    variants = {
        "bold": "dramatic high contrast hero composition, strong subject on right, dark negative space on left",
        "cinematic": "cinematic atmospheric wide composition, dramatic lighting, depth and premium color grading",
        "minimal": "minimal elegant editorial composition, one clear symbolic object, uncluttered background",
    }
    outputs = []
    for style, direction in variants.items():
        background = output_dir / f"background_{style}.jpg"
        prompt = (
            "Create a premium YouTube thumbnail background, landscape 16:9. "
            f"Topic: {title}. Visual idea: {visual_hint}. Art direction: {direction}. "
            "Use a clear central subject and polished cinematic lighting. "
            "Do not include any text, letters, numbers, logos, watermark, or typography."
        )
        generated = generate_image(prompt, background)
        if generated:
            with Image.open(background) as opened:
                base = ImageOps.fit(opened.convert("RGB"), SIZE, method=Image.Resampling.LANCZOS)
        else:
            # Reliable fallback: a designed gradient-like canvas, still independent of the MP4.
            base = Image.new("RGB", SIZE, (9, 18, 38))
            draw = ImageDraw.Draw(base)
            draw.ellipse((700, -180, 1450, 650), fill=(25, 70, 105))
            draw.ellipse((850, 120, 1320, 720), fill=(24, 42, 75))
        outputs.append(_render(base, title, channel, output_dir / f"thumbnail_{style}.jpg", style))
    return outputs
