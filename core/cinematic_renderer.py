"""Deterministic, code-generated vertical motion graphics for reviewed news Shorts.

This renderer creates original graphic scenes; it does not fabricate photographs, maps,
statistics, or presenter footage. Every factual label is supplied by the reviewed script.
"""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, FPS = 1080, 1920, 30
BG = (8, 13, 24)
WHITE = (245, 247, 250)
MUTED = (157, 171, 190)
CYAN = (65, 210, 224)
AMBER = (255, 184, 77)
RED = (245, 91, 91)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          max_width: int) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), trial, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _text_block(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int],
                font: ImageFont.FreeTypeFont, fill: tuple[int, int, int],
                max_width: int, line_gap: int = 18, max_lines: int = 5) -> int:
    x, y = xy
    lines = _wrap(draw, text, font, max_width)[:max_lines]
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill, stroke_width=0)
        y += font.size + line_gap
    return y


def _fit_text_block(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int],
                    max_width: int, max_height: int, start_size: int = 65,
                    min_size: int = 32) -> int:
    """Draw every headline line while shrinking the font to stay in its reserved area."""
    x, y = xy
    for size in range(start_size, min_size - 1, -2):
        font = _font(size, True)
        lines = _wrap(draw, text, font, max_width)
        line_gap = max(8, size // 4)
        if len(lines) * (size + line_gap) <= max_height:
            for line in lines:
                draw.text((x, y), line, font=font, fill=WHITE, stroke_width=0)
                y += size + line_gap
            return y
    font = _font(min_size, True)
    line_gap = max(8, min_size // 4)
    for line in _wrap(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=WHITE, stroke_width=0)
        y += min_size + line_gap
    return y


def _scene_frame(scene: dict[str, Any], index: int, elapsed: float,
                 duration: float, total_elapsed: float) -> Image.Image:
    """Pure frame function: identical scene/time inputs produce identical pixels."""
    im = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(im, "RGBA")
    progress = min(max(elapsed / max(duration, 0.001), 0.0), 1.0)
    # Subtle animated technical grid and restrained moving accent.
    for x in range(0, WIDTH, 72):
        d.line((x, 0, x, HEIGHT), fill=(55, 77, 103, 34), width=1)
    for y in range(0, HEIGHT, 72):
        d.line((0, y, WIDTH, y), fill=(55, 77, 103, 28), width=1)
    sweep_y = int((total_elapsed * 115) % HEIGHT)
    d.line((0, sweep_y, WIDTH, sweep_y), fill=(65, 210, 224, 24), width=3)

    style = str(scene.get("visual_style") or scene.get("scene_type") or "")
    if style not in {"visual_investigation", "broadcast_news"}:
        style = "visual_investigation" if index % 2 == 0 else "broadcast_news"
    accent = CYAN if style == "visual_investigation" else AMBER
    label = str(scene.get("label") or scene.get("caption") or f"SCENE {index + 1}").strip()
    headline = str(scene.get("headline") or scene.get("caption") or label).strip()
    kicker = str(scene.get("kicker") or ("VISUAL INVESTIGATION" if style == "visual_investigation" else "NEWS BRIEFING"))
    source_ids = scene.get("source_ids") or []
    source_line = "SOURCE " + "  /  ".join(str(x) for x in source_ids[:4]) if source_ids else "SOURCE REFERENCES IN REVIEW FILE"

    # Header / chapter marker.
    d.rounded_rectangle((64, 82, 1016, 156), radius=18, fill=(17, 28, 45, 235),
                        outline=(*accent, 180), width=2)
    d.ellipse((91, 108, 119, 136), fill=accent)
    d.text((145, 101), kicker.upper()[:44], font=_font(30, True), fill=WHITE)
    d.text((930, 104), f"{index + 1:02d}", font=_font(34, True), fill=accent)

    # Main evidence / headline card animates into place without using fake media.
    slide = int(28 * (1 - min(progress * 5, 1)))
    top = 270 + slide
    d.rounded_rectangle((64, top, 1016, 1090), radius=34, fill=(17, 27, 43, 245),
                        outline=(83, 112, 143, 190), width=3)
    d.rectangle((64, top, 78, 1090), fill=accent)
    d.text((112, top + 64), "KEY DEVELOPMENT" if style == "broadcast_news" else "EVIDENCE FILE",
           font=_font(28, True), fill=accent)
    # Reserve a fixed headline area so long facts never collide with graphic elements.
    _fit_text_block(d, headline, (112, top + 142), 820, 340, 65, 32)

    if style == "visual_investigation":
        # Original evidence-board motif: source cards and connecting lines, not a real map.
        # Keep circles below the headline and the descriptor below the circles.
        center_y = 900
        d.line((190, center_y, 540, center_y), fill=(*accent, 210), width=5)
        d.line((540, center_y, 870, center_y), fill=(*accent, 210), width=5)
        for j, (cx, title) in enumerate(((190, "CLAIM"), (540, "CONTEXT"), (870, "SOURCE"))):
            r = 68 if j != 1 else 82
            d.ellipse((cx-r, center_y-r, cx+r, center_y+r),
                      fill=(23, 43, 62, 255), outline=(*accent, 240), width=5)
            box = d.textbbox((0, 0), title, font=_font(22, True))
            d.text((cx-(box[2]-box[0])/2, center_y-10), title, font=_font(22, True), fill=WHITE)
        d.text((112, 1010), "TRACE THE EVIDENCE", font=_font(25, True), fill=MUTED)
    else:
        # Broadcast lower-third starts below the reserved headline area.
        d.rounded_rectangle((112, 790, 968, 950), radius=22,
                            fill=(34, 43, 56, 255), outline=(*accent, 180), width=2)
        d.text((148, 812), "THE STORY SO FAR", font=_font(25, True), fill=accent)
        _text_block(d, label, (148, 852), _font(31, True), WHITE, 770, 10, 2)
        d.text((112, 1000), "FIELD NOTES  /  VERIFIED CONTEXT", font=_font(25, True), fill=MUTED)

    # Source reference band, persistent and legible.
    d.rounded_rectangle((64, 1160, 1016, 1290), radius=18, fill=(13, 22, 36, 245),
                        outline=(71, 93, 119, 160), width=2)
    d.text((96, 1184), "RESEARCH TRAIL", font=_font(22, True), fill=accent)
    d.text((96, 1230), source_line[:72], font=_font(25), fill=WHITE)

    # Bottom timeline gives the scene a deliberate sense of progression.
    d.rounded_rectangle((64, 1370, 1016, 1384), radius=7, fill=(48, 62, 80, 255))
    d.rounded_rectangle((64, 1370, 64 + int(952 * progress), 1384), radius=7, fill=accent)
    d.text((64, 1430), f"AI STUDIO  /  {index + 1:02d}", font=_font(24, True), fill=MUTED)
    d.text((1016, 1430), f"{int(progress * 100):02d}%", font=_font(24, True),
           fill=WHITE, anchor="ra")
    # Keep the lower subtitle-safe band completely clear; captions are composited here.
    return im


def render_cinematic_video(scenes: list[dict[str, Any]], durations: list[float],
                           output_path: str | Path, fps: int = FPS) -> dict[str, Any]:
    """Render deterministic original motion graphics to an H.264 silent video."""
    if not scenes or len(scenes) != len(durations):
        raise ValueError("Scenes and durations must be non-empty lists of equal length.")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in durations):
        raise ValueError("Every scene duration must be a finite positive number.")
    if fps < 1 or fps > 60:
        raise ValueError("fps must be between 1 and 60.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required for cinematic rendering.")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    total = sum(float(x) for x in durations) + 0.25 * (len(durations) - 1) + 0.6
    command = [
        ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(fps), "-i", "-", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    elapsed_total = 0.0
    frame_count = 0
    try:
        assert process.stdin is not None
        for index, (scene, duration) in enumerate(zip(scenes, durations)):
            segment = float(duration) + (0.25 if index < len(scenes) - 1 else 0.0)
            frames = max(1, int(round(segment * fps)))
            for frame_no in range(frames):
                t = min(frame_no / fps, float(duration))
                frame = _scene_frame(scene, index, t, float(duration), elapsed_total + t)
                process.stdin.write(frame.tobytes())
                frame_count += 1
            elapsed_total += segment
        # Brief closing hold; the final scene remains visible.
        last = _scene_frame(scenes[-1], len(scenes)-1, float(durations[-1]),
                            float(durations[-1]), elapsed_total)
        for _ in range(max(1, int(round(0.6 * fps)))):
            process.stdin.write(last.tobytes())
            frame_count += 1
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        code = process.wait()
        if code:
            raise RuntimeError(f"FFmpeg cinematic render failed: {stderr.decode(errors='replace')[-2000:]}")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("Cinematic renderer did not create a non-empty video.")
    return {"video_path": str(out), "duration_seconds": round(total, 3),
            "fps": fps, "width": WIDTH, "height": HEIGHT, "frame_count": frame_count,
            "renderer": "original_pillow_motion_graphics"}
