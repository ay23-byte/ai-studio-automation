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


# Function words of Hindi written in Latin letters. None of them is an English word, so a
# caption set where they are common is Hinglish, whatever script it is written in.
_ROMAN_HINDI = frozenset("""
hai hain tha thi ka ki ke ko aur nahi nahin yeh ye woh kya kyun kyunki mein bhi liye lekin
agar toh jab tab kaise hota hoti hote karta karti karte raha rahi rahe gaya gayi gaye hum aap
iska iske uska uske unka unke isse usse apne apni sirf bahut kuch sab phir jo se par tak wala
wali wale hua hui hue kar diya diye kiya kiye jata jati jate sakta sakti sakte chahiye
""".split())
_WORD = re.compile(r"[A-Za-z']+")


def english_subtitle_report(texts) -> dict[str, Any]:
    """Check that subtitle text is English: no Devanagari and no Latin-script Hindi.

    Returns {"status": "PASS" | "FAIL", "issues": [...], ...}. Documentaries may be narrated
    in Hindi or Hinglish, but their subtitles are English only.
    """
    texts = [str(text or "") for text in texts]
    devanagari = [index for index, text in enumerate(texts) if _DEVANAGARI.search(text)]
    words = hindi = 0
    hinglish_lines = []
    for index, text in enumerate(texts):
        tokens = [token.lower() for token in _WORD.findall(text)]
        found = sum(1 for token in tokens if token in _ROMAN_HINDI)
        words += len(tokens)
        hindi += found
        if found >= 2 and found / max(len(tokens), 1) >= 0.25:
            hinglish_lines.append(index)
    ratio = hindi / words if words else 0.0
    issues = []
    if devanagari:
        issues.append(f"{len(devanagari)} subtitle lines contain Devanagari (first: line {devanagari[0] + 1}).")
    if hinglish_lines and (ratio >= 0.06 or len(hinglish_lines) >= max(2, len(texts) // 20)):
        issues.append(
            f"{len(hinglish_lines)} subtitle lines read as Hindi in Latin letters "
            f"(first: line {hinglish_lines[0] + 1}: {texts[hinglish_lines[0]][:80]!r})."
        )
    return {
        "status": "FAIL" if issues else "PASS", "issues": issues, "lines": len(texts),
        "devanagari_lines": len(devanagari), "roman_hindi_lines": len(hinglish_lines),
        "roman_hindi_word_ratio": round(ratio, 4),
    }


def documentary_subtitle_language(requested, policy: str | None = None) -> str:
    """The subtitle language a documentary may use: English, unless the policy is lifted.

    Narration may be Hindi, Hinglish or English. Subtitles are English only, so a request
    for Hindi, Hinglish or as-spoken subtitles is refused here rather than rendered.
    ``DOCUMENTARY_SUBTITLE_POLICY=any`` restores the free choice for non-channel use.
    """
    import os

    target = normalize_subtitle_language(requested)
    policy = str(policy if policy is not None else os.getenv("DOCUMENTARY_SUBTITLE_POLICY", "english_only")).strip().lower()
    if policy == "any" or target == "english":
        return target
    raise ValueError(
        f"Documentary subtitles are English only; '{target}' subtitles were requested. "
        "Narration can stay in Hindi or Hinglish. Remove SUBTITLE_LANGUAGE/--subtitle-language "
        "or set it to english."
    )


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
