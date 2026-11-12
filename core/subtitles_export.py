"""Sidecar caption files (SRT and WebVTT) written next to every render.

Burned-in ASS captions cannot be toggled, translated or indexed by YouTube.
These sidecars use the same measured narration timings as the burned-in
captions and can be uploaded as a caption track or edited by hand.
"""
from __future__ import annotations

import re
from pathlib import Path


def _clock(seconds: float, separator: str) -> str:
    millis = int(round(max(float(seconds), 0.0) * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _clean(text) -> str:
    value = re.sub(r"\{[^}]*\}", "", str(text or ""))        # ASS override tags
    value = value.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", value).strip()


def split_cues(timings, max_chars: int = 84, max_seconds: float = 6.0):
    """Split long narration phrases into readable cues, keeping timing proportional."""
    cues = []
    for item in timings or []:
        try:
            start, end, text = float(item[0]), float(item[1]), _clean(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if not text or end <= start:
            continue
        words = text.split()
        pieces = max(1, -(-len(text) // max_chars), int(-(-(end - start) // max_seconds)))
        pieces = min(pieces, len(words))
        per_piece = -(-len(words) // pieces)
        groups = [" ".join(words[i:i + per_piece]) for i in range(0, len(words), per_piece)]
        weights = [max(len(group), 1) for group in groups]
        cursor = start
        for group, weight in zip(groups, weights):
            duration = (end - start) * weight / sum(weights)
            cues.append((cursor, cursor + duration, group))
            cursor += duration
    # Enforce strictly increasing, non-overlapping cues.
    fixed, previous_end = [], 0.0
    for start, end, text in cues:
        start = max(start, previous_end)
        if end - start < 0.05:
            continue
        fixed.append((start, end, text))
        previous_end = end
    return fixed


def to_srt(timings) -> str:
    blocks = []
    for number, (start, end, text) in enumerate(split_cues(timings), start=1):
        blocks.append(f"{number}\n{_clock(start, ',')} --> {_clock(end, ',')}\n{text}\n")
    return "\n".join(blocks)


def to_vtt(timings) -> str:
    blocks = ["WEBVTT\n"]
    for start, end, text in split_cues(timings):
        blocks.append(f"{_clock(start, '.')} --> {_clock(end, '.')}\n{text}\n")
    return "\n".join(blocks)


def write_sidecars(timings, directory, stem: str = "captions", language: str = "") -> dict:
    """Write ``<stem>[.<lang>].srt`` and ``.vtt``; returns their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f".{language}" if language else ""
    srt_path = directory / f"{stem}{suffix}.srt"
    vtt_path = directory / f"{stem}{suffix}.vtt"
    srt_path.write_text(to_srt(timings), encoding="utf-8")
    vtt_path.write_text(to_vtt(timings), encoding="utf-8")
    return {"srt": str(srt_path), "vtt": str(vtt_path), "cue_count": len(split_cues(timings))}


LANGUAGE_CODES = {"english": "en", "hindi": "hi", "hinglish": "hi-Latn"}
