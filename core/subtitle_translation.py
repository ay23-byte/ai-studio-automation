"""English-only subtitle translation for long-form documentaries.

Translation is deliberately separate from TTS: it never changes the narration audio,
sentence timing, research text, or source claims.
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.script_engine import _ask_gemini

_BATCH_SIZE = 35
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _translate_batch(items: list[dict[str, Any]]) -> dict[int, str]:
    payload = [{"id": int(item["id"]), "text": str(item["text"])} for item in items]
    prompt = (
        "Translate the following documentary subtitle strings into natural, concise, "
        "accurate English. The source may be Hindi, Hinglish, or English. "
        "Return ONLY JSON in the exact shape {\"translations\":[{\"id\":0,\"text\":\"...\"}]}. "
        "Preserve names, dates, numbers, technical terms, uncertainty, and meaning. "
        "Do not summarize, omit, add facts, or combine items. Keep one output per input ID. "
        "Every output must be English in Latin script; never use Devanagari. "
        "Keep subtitle phrasing readable on screen.\nINPUT:\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw = _ask_gemini(prompt)
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.I)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError("English subtitle translation returned invalid JSON.") from exc
    rows = parsed.get("translations") if isinstance(parsed, dict) else None
    if not isinstance(rows, list) or len(rows) != len(items):
        raise RuntimeError("English subtitle translation returned a different number of lines.")
    expected = {int(item["id"]) for item in items}
    result: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            raise RuntimeError("English subtitle translation contains a malformed row.")
        item_id = row["id"]
        text = str(row.get("text") or "").strip()
        if item_id not in expected or item_id in result or not text:
            raise RuntimeError("English subtitle translation contains missing, duplicate, or empty lines.")
        if _DEVANAGARI.search(text):
            raise RuntimeError(f"Subtitle line {item_id} still contains Devanagari; refusing a Hindi subtitle render.")
        result[item_id] = text
    if set(result) != expected:
        raise RuntimeError("English subtitle translation omitted one or more input lines.")
    return result


def translate_longform_subtitles(
    timings: list[tuple[float, float, str]],
    sections: list[dict[str, Any]],
) -> tuple[list[tuple[float, float, str]], list[str]]:
    """Translate spoken captions and chapter labels while preserving timing exactly."""
    entries: list[dict[str, Any]] = []
    caption_ids: list[int] = []
    title_ids: list[int] = []
    for start, end, text in timings:
        caption_ids.append(len(entries))
        entries.append({"id": len(entries), "text": str(text)})
    for section in sections:
        title_ids.append(len(entries))
        entries.append({"id": len(entries), "text": str(section.get("title") or "")})
    translated: dict[int, str] = {}
    for offset in range(0, len(entries), _BATCH_SIZE):
        batch = entries[offset:offset + _BATCH_SIZE]
        translated.update(_translate_batch(batch))
    translated_timings = [
        (start, end, translated[item_id])
        for (start, end, _text), item_id in zip(timings, caption_ids)
    ]
    translated_titles = [translated[item_id] for item_id in title_ids]
    return translated_timings, translated_titles
