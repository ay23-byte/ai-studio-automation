"""Align script units (Shorts scenes, documentary chapters) to real narration.

Root cause addressed: Shorts gave every scene ``total / n`` seconds and
documentaries split time by word count. Neither used the measured speech
timings the TTS stage already returns, so visuals and scene captions drifted
from the voice whenever scenes had different lengths, speaking rates or pauses.
"""
from __future__ import annotations

import re


def _norm_len(text) -> int:
    return len(re.sub(r"\s+", " ", str(text or "")).strip())


def _time_at(position: float, chunks) -> float:
    """Map a character position in the spoken text to a time on the voice track."""
    cursor = 0.0
    for index, (start, end, length) in enumerate(chunks):
        chunk_end = cursor + length
        if position <= chunk_end + 1.5:
            if position >= chunk_end - 1.5:
                # The boundary coincides with a phrase end: cut inside the pause.
                if index + 1 < len(chunks):
                    return (end + chunks[index + 1][0]) / 2.0
                return end
            fraction = max(0.0, (position - cursor) / max(length, 1))
            return start + fraction * (end - start)
        cursor = chunk_end + 1  # joining space
    return chunks[-1][1] if chunks else 0.0


def align_units(texts, timings, total: float, min_duration: float = 1.2):
    """Return ``[(start, end), ...]`` for each text unit on the narration track.

    ``texts`` are the units in spoken order; ``timings`` are the
    ``(start, end, text)`` phrase timings from ``core.voice.synthesise`` for the
    same narration. The spans are contiguous, start at 0 and end at ``total``.
    Falls back to a length-proportional split when timings are unusable.
    """
    count = len(texts)
    if count == 0:
        return []
    total = max(float(total), 0.0)
    lengths = [max(_norm_len(text), 1) for text in texts]
    unit_chars = sum(lengths) + (count - 1)

    chunks = []
    for item in timings or []:
        try:
            start, end, text = float(item[0]), float(item[1]), item[2]
        except (TypeError, ValueError, IndexError):
            chunks = []
            break
        if end > start:
            chunks.append((start, end, max(_norm_len(text), 1)))
    chunk_chars = sum(c[2] for c in chunks) + max(len(chunks) - 1, 0)

    boundaries = [0.0]
    cumulative = 0
    for index in range(count - 1):
        cumulative += lengths[index] + 1
        fraction = (cumulative - 0.5) / unit_chars
        if chunks:
            boundaries.append(_time_at(fraction * chunk_chars, chunks))
        else:
            boundaries.append(fraction * total)
    boundaries.append(total)

    # Monotonic and inside the track.
    for index in range(1, len(boundaries)):
        boundaries[index] = min(max(boundaries[index], boundaries[index - 1]), total)

    # No unit may be shorter than a transition can display.
    floor = min(float(min_duration), total / count if count else 0.0)
    durations = [boundaries[i + 1] - boundaries[i] for i in range(count)]
    for _ in range(count * 4):
        short = min(range(count), key=lambda i: durations[i])
        if durations[short] >= floor - 1e-6:
            break
        donor = max(range(count), key=lambda i: durations[i])
        take = min(floor - durations[short], durations[donor] - floor)
        if take <= 1e-6:
            break
        durations[short] += take
        durations[donor] -= take
    spans, cursor = [], 0.0
    for duration in durations:
        spans.append((cursor, cursor + duration))
        cursor += duration
    if spans:
        spans[-1] = (spans[-1][0], total)
    return spans
