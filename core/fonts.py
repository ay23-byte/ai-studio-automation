"""Script-aware font resolution for captions, cards and thumbnails.

Root cause addressed: every renderer hard-coded Arial/DejaVu (Latin only), so
Hindi (Devanagari) titles, slates and thumbnails rendered as empty boxes, and
ASS captions named ``Noto Sans`` which has no Devanagari glyphs.
"""
from __future__ import annotations

import functools
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")

_LATIN_BOLD = (
    "NotoSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)
_LATIN_REGULAR = (
    "NotoSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_DEVANAGARI_BOLD = (
    "NotoSansDevanagari-Bold.ttf", "C:/Windows/Fonts/NirmalaB.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "C:/Windows/Fonts/mangalb.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
)
_DEVANAGARI_REGULAR = (
    "NotoSansDevanagari-Regular.ttf", "C:/Windows/Fonts/Nirmala.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "C:/Windows/Fonts/mangal.ttf",
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
)


def contains_devanagari(text) -> bool:
    return bool(_DEVANAGARI.search(str(text or "")))


def _search_dirs() -> list[Path]:
    dirs = []
    configured = os.getenv("SUBTITLE_FONTS_DIR", "").strip()
    if configured:
        dirs.append(Path(configured))
    dirs += [ROOT / "assets" / "fonts", Path.home() / ".fonts",
             Path.home() / ".local" / "share" / "fonts"]
    return [d for d in dirs if d.is_dir()]


def bundled_or_user_fonts_dir() -> str:
    """First configured/user font directory that actually contains fonts."""
    for directory in _search_dirs():
        if any(directory.glob("*.ttf")) or any(directory.glob("*.otf")):
            return str(directory)
    return ""


@functools.lru_cache(maxsize=None)
def _resolve(candidates: tuple) -> str:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_absolute() or ":" in candidate[:3]:
            if path.is_file():
                return str(path)
            continue
        for directory in _search_dirs():
            if (directory / candidate).is_file():
                return str(directory / candidate)
    return ""


def font_path(text="", bold=False) -> str:
    """Path to a font covering ``text`` (Devanagari-aware); '' if none found."""
    if contains_devanagari(text):
        found = _resolve(_DEVANAGARI_BOLD if bold else _DEVANAGARI_REGULAR)
        if found:
            return found
    return _resolve(_LATIN_BOLD if bold else _LATIN_REGULAR)


def load_font(size, bold=False, text=""):
    """Return a Pillow font covering ``text``; never raises."""
    from PIL import ImageFont
    path = font_path(text, bold)
    if path:
        try:
            return ImageFont.truetype(path, int(size))
        except OSError:
            pass
    try:
        return ImageFont.load_default(int(size))
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def ass_font_name(text_or_language="") -> str:
    """ASS/libass font family for a caption language or sample text."""
    value = str(text_or_language or "")
    if value.strip().lower() == "hindi" or contains_devanagari(value):
        return os.getenv("SUBTITLE_FONT_DEVANAGARI", "Noto Sans Devanagari")
    return os.getenv("SUBTITLE_FONT_LATIN", "Noto Sans")


def devanagari_support() -> dict:
    """Diagnostics used by health checks and QA reports."""
    try:
        from PIL import features
        raqm = bool(features.check("raqm"))
    except Exception:
        raqm = False
    return {
        "devanagari_font": font_path("हिंदी") if _resolve(_DEVANAGARI_REGULAR) else "",
        "latin_font": font_path("abc"),
        "pillow_complex_shaping": raqm,
        "note": ("Burned-in captions are shaped by libass. Pillow cards need "
                 "libraqm for correct Devanagari conjuncts; install libfribidi/"
                 "libharfbuzz (Ubuntu: libraqm0) when rendering Hindi cards."),
    }
