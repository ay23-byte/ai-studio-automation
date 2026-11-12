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


_TARGETS = {
    "english": ("natural, concise, accurate English",
                "Every output must be English in Latin script; never use Devanagari."),
    "hindi": ("natural, concise, accurate Hindi",
              "Every output must be Hindi written in Devanagari script; keep proper nouns, "
              "units and technical terms recognisable."),
    "hinglish": ("natural, conversational Hinglish (Hindi written in Latin/Roman script, "
                 "with common English technical words kept in English)",
                 "Every output must use Latin script only; never use Devanagari."),
}


def normalize_subtitle_language(value, default="english") -> str:
    """Return english | hindi | hinglish | source for a user-supplied setting."""
    text = str(value or "").strip().lower()
    aliases = {"en": "english", "eng": "english", "hi": "hindi", "hin": "hindi",
               "hi-latn": "hinglish", "roman": "hinglish", "same": "source",
               "spoken": "source", "none": "source", "off": "source", "original": "source"}
    text = aliases.get(text, text)
    return text if text in {"english", "hindi", "hinglish", "source"} else default


def _translate_batch(items: list[dict[str, Any]], target: str = "english") -> dict[int, str]:
    payload = [{"id": int(item["id"]), "text": str(item["text"])} for item in items]
    description, script_rule = _TARGETS[target]
    prompt = (
        f"Translate the following documentary subtitle strings into {description}. "
        "The source may be Hindi, Hinglish, or English. "
        "Return ONLY JSON in the exact shape {\"translations\":[{\"id\":0,\"text\":\"...\"}]}. "
        "Preserve names, dates, numbers, technical terms, uncertainty, and meaning. "
        "Do not summarize, omit, add facts, or combine items. Keep one output per input ID. "
        f"{script_rule} "
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
        if target != "hindi" and _DEVANAGARI.search(text):
            raise RuntimeError(f"Subtitle line {item_id} still contains Devanagari; refusing a Hindi subtitle render.")
        result[item_id] = text
    if set(result) != expected:
        raise RuntimeError("English subtitle translation omitted one or more input lines.")
    return result


def needs_translation(texts, target_language: str, source_language: str | None) -> bool:
    """Whether captions must be sent to the language model at all.

    Root cause addressed: English narration was always "translated to English",
    which made every documentary depend on an LLM call after the expensive
    render inputs were produced, and broke the key-free demo build.
    """
    target = normalize_subtitle_language(target_language)
    if target == "source":
        return False
    source = str(source_language or "").strip().lower()
    if not source:
        return True  # unknown spoken language: keep the original always-translate behaviour
    has_devanagari = any(_DEVANAGARI.search(str(text or "")) for text in texts)
    if target == "hindi":
        return not (source == "hindi" and has_devanagari)
    if has_devanagari:
        return True
    return source != target


def translate_longform_subtitles(
    timings: list[tuple[float, float, str]],
    sections: list[dict[str, Any]],
    target_language: str = "english",
    source_language: str | None = None,
) -> tuple[list[tuple[float, float, str]], list[str]]:
    """Translate spoken captions and chapter labels while preserving timing exactly.

    ``target_language``: english (default), hindi, hinglish, or ``source`` to
    show the narration as spoken. When ``source_language`` is supplied and the
    captions are already in the target language, no model call is made.
    """
    target = normalize_subtitle_language(target_language)
    texts = [str(text) for _, _, text in timings] + [str(sec.get("title") or "") for sec in sections]
    if not needs_translation(texts, target, source_language):
        return list(timings), [str(section.get("title") or "") for section in sections]
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
        translated.update(_translate_batch(batch) if target == "english"
                          else _translate_batch(batch, target))
    translated_timings = [
        (start, end, translated[item_id])
        for (start, end, _text), item_id in zip(timings, caption_ids)
    ]
    translated_titles = [translated[item_id] for item_id in title_ids]
    return translated_timings, translated_titles
