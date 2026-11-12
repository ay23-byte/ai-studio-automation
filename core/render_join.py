"""Segment joining shared by the Shorts and documentary editors.

Each rendered segment ``i`` lasts ``durations[i] + fade`` seconds: its visible
time plus the overlap consumed by the transition into the next segment. Joining
``n`` segments therefore yields ``sum(durations) + fade`` seconds, which means a
joined batch is itself a valid segment. That property lets long timelines be
reduced in bounded batches instead of opening every file in one FFmpeg process.

Root causes addressed:

* One ``filter_complex`` with 100+ inputs and a 100-deep ``xfade`` chain
  exhausted memory/file handles on 7 GB CI runners for long documentaries.
* Builds without ``xfade`` crashed; they now use frame-accurate hard cuts.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import ffmpeg_utils

INTERMEDIATE_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
                    "-r", "30", "-video_track_timescale", "15360"]


def batch_size() -> int:
    try:
        value = int(os.getenv("RENDER_JOIN_BATCH", "16"))
    except ValueError:
        value = 16
    return max(2, min(value, 64))


def video_chain(durations, transitions, fade: float, out_label: str = "vjoin",
                use_xfade: bool | None = None) -> str:
    """Filter chain joining inputs ``0..n-1`` into ``[out_label]``."""
    count = len(durations)
    if count == 1:
        return f"[0:v]null[{out_label}]"
    if use_xfade is None:
        use_xfade = ffmpeg_utils.xfade_supported()
    if not use_xfade:
        visible = [float(d) for d in durations[:-1]] + [float(durations[-1]) + fade]
        return ffmpeg_utils.trimmed_concat_chain(visible, out_label)
    chain, previous, offset = [], "[0:v]", 0.0
    for index in range(1, count):
        offset += float(durations[index - 1])
        label = f"[{out_label}]" if index == count - 1 else f"[xj{index}]"
        name = (transitions[index - 1] if transitions and index - 1 < len(transitions) else None) or "fade"
        chain.append(f"{previous}[{index}:v]xfade=transition={name}:duration={fade}:offset={offset:.3f}{label}")
        previous = label
    return ";".join(chain)


def reduce_segments(parts, durations, transitions, fade: float, work_dir,
                    limit: int | None = None, prefix: str = "join"):
    """Pre-join batches until at most ``limit`` segments remain.

    Returns ``(parts, durations, transitions)`` with the same timing contract.
    """
    limit = limit or batch_size()
    parts = [Path(p) for p in parts]
    durations = [float(d) for d in durations]
    transitions = list(transitions or [])
    transitions += ["fade"] * (len(parts) - 1 - len(transitions))
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    level = 0
    while len(parts) > limit:
        next_parts, next_durations, next_transitions = [], [], []
        for group_index, start in enumerate(range(0, len(parts), limit)):
            group = parts[start:start + limit]
            group_durations = durations[start:start + limit]
            group_transitions = transitions[start:start + len(group) - 1]
            if start + len(group) < len(parts):
                next_transitions.append(transitions[start + len(group) - 1])
            if len(group) == 1:
                next_parts.append(group[0])
                next_durations.append(group_durations[0])
                continue
            out = work_dir / f"{prefix}_{level}_{group_index:03d}.mp4"
            inputs = []
            for path in group:
                inputs += ["-i", str(path)]
            total = sum(group_durations) + fade
            ffmpeg_utils.run([
                ffmpeg_utils.ffmpeg_bin(), "-y", "-loglevel", "error", *inputs,
                "-filter_complex", video_chain(group_durations, group_transitions, fade, "vjoin"),
                "-map", "[vjoin]", "-an", "-t", f"{total:.3f}", *INTERMEDIATE_ENC, str(out),
            ])
            next_parts.append(out)
            next_durations.append(sum(group_durations))
        parts, durations, transitions = next_parts, next_durations, next_transitions
        level += 1
    return parts, durations, transitions
