"""Speech-aligned English subtitles generated from the FINAL narration."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import V2Config
from .models import NarrationSegment

DEVANAGARI = re.compile(r"[\u0900-\u097f]")


def timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _split_text(text: str, max_words: int, max_chars: int) -> list[str]:
    """Wrap a sentence into readable caption lines without fragmenting every few words."""
    words = text.replace("\n", " ").split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and (len(current) >= max_words or len(candidate) > max_chars):
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _caption_cues(text: str, max_words: int, max_chars: int) -> list[str]:
    """Group wrapped lines into one- or two-line cues, never tiny one-line fragments."""
    lines = _split_text(text, max_words, max_chars)
    return ["\n".join(lines[index:index + 2]) for index in range(0, len(lines), 2)]


def build_ass(segments: list[NarrationSegment], config: V2Config) -> str:
    rules = config.rules_for("documentary")
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Alignment, MarginV\n"
        "Style: Default,Noto Sans,46,&H00FFFFFF,2,64\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Text\n"
    )
    events = []
    def ass_time(value: float) -> str:
        h = int(value // 3600)
        m = int((value % 3600) // 60)
        s = value % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"
    for segment in segments:
        cues = _caption_cues(segment.text, rules.subtitle_max_words, rules.subtitle_max_chars)
        weights = [max(1, len(cue.replace("\n", " "))) for cue in cues]
        total_weight = sum(weights)
        span = max(0.01, segment.end - segment.start)
        cursor = segment.start
        for cue, weight in zip(cues, weights):
            cue_end = min(segment.end, cursor + span * weight / total_weight)
            if cue_end <= cursor:
                cue_end = cursor + 0.01
            display = cue.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", r"\N")
            events.append(
                f"Dialogue: 0,{ass_time(cursor)},{ass_time(cue_end)},Default,{display}"
            )
            cursor = cue_end
    return header + "\n".join(events) + "\n"


@dataclass
class SubtitleAssessment:
    accepted: bool
    issues: list[str]
    cues: int


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = lines[1].split("-->", 1)

        def parse(value: str) -> float:
            value = value.strip().replace(",", ".")
            parts = value.split(":")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

        cues.append((parse(left), parse(right), " ".join(lines[2:])))
    return cues


def validate_subtitles(srt: str, segments: list[NarrationSegment], config: V2Config,
                       video_duration: float | None = None) -> SubtitleAssessment:
    issues: list[str] = []
    cues = parse_srt(srt)
    if len(cues) < len(segments):
        issues.append(f"expected at least one cue per narration segment; got {len(cues)} for {len(segments)}")
    previous_end = -1.0
    for index, (start, end, text) in enumerate(cues, start=1):
        if end <= start:
            issues.append(f"cue {index}: end is not after start")
        if start < previous_end - 0.001:
            issues.append(f"cue {index}: overlapping timing")
        previous_end = max(previous_end, end)
        if DEVANAGARI.search(text):
            issues.append(f"cue {index}: non-English text in English subtitle track")
        if len(text) > 130:
            issues.append(f"cue {index}: text exceeds readable length")
    if video_duration and cues and cues[-1][1] > video_duration + 2.0:
        issues.append("last subtitle cue ends after the video")
    return SubtitleAssessment(not issues, issues, len(cues))
