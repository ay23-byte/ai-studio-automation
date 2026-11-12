"""Deterministic content QA for Shorts scripts (no model call, no network).

Root cause addressed: a Shorts script went straight from the language model to
narration and rendering. Truncated JSON, the wrong language/script, a 300-word
"Short", empty scenes or promised financial returns were only discovered by a
human after the video existed (or after it was uploaded by the schedule).
"""
from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")

# Claims an educational channel must never make, in English and Roman/Devanagari Hindi.
_RISKY_PATTERNS = [
    (r"\bguaranteed?\s+(returns?|profit|income|results?)\b", "promises guaranteed returns"),
    (r"\b(risk[- ]free|no[- ]risk|zero[- ]risk)\b", "claims an investment is risk free"),
    (r"\b(double|triple)\s+your\s+money\b", "promises to multiply money"),
    (r"\bget\s+rich\s+(quick|fast)\b", "get-rich-quick claim"),
    (r"\b(paisa|paise)\s+(double|dugna)\b", "promises to multiply money"),
    (r"\bpakka\s+(profit|munafa|return)\b", "promises guaranteed returns"),
    (r"गारंटीड\s*(रिटर्न|मुनाफ़ा|मुनाफा)", "promises guaranteed returns"),
    (r"\b(cures?|guaranteed\s+cure)\s+(cancer|diabetes|covid)\b", "unverifiable medical claim"),
    (r"\b(buy|sell)\s+(this|these)\s+(stock|share|coin)s?\s+(now|today)\b", "personal investment advice"),
]

LIMITS = {
    "min_scenes": 3, "max_scenes": 9,
    "min_scene_words": 4, "max_scene_words": 40,
    "min_total_words": 40, "max_total_words": 190,
    "max_title_chars": 100,
}


def _words(text) -> int:
    return len(str(text or "").split())


def _script_mix(text):
    value = str(text or "")
    return len(_DEVANAGARI.findall(value)), len(_LATIN.findall(value))


def validate_shorts_script(script, language: str = "english") -> dict:
    """Return ``{"status": PASS|REVIEW|FAIL, "issues": [...], "warnings": [...], "metrics": {...}}``."""
    issues, warnings = [], []
    if not isinstance(script, dict):
        return {"status": "FAIL", "issues": ["Script is not a JSON object."], "warnings": [], "metrics": {}}
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return {"status": "FAIL", "issues": ["Script has no scenes."], "warnings": [], "metrics": {}}
    title = str(script.get("title") or "").strip()
    if not title:
        issues.append("Title is missing.")
    elif len(title) > LIMITS["max_title_chars"]:
        warnings.append(f"Title is {len(title)} characters; YouTube truncates beyond 100.")

    if not LIMITS["min_scenes"] <= len(scenes) <= LIMITS["max_scenes"]:
        issues.append(f"Script has {len(scenes)} scenes; expected {LIMITS['min_scenes']}-{LIMITS['max_scenes']}.")
    scene_words, spoken = [], []
    for number, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            issues.append(f"Scene {number} is not an object.")
            continue
        say = str(scene.get("say") or "").strip()
        count = _words(say)
        scene_words.append(count)
        spoken.append(say)
        if not say:
            issues.append(f"Scene {number} has no narration.")
        elif count < LIMITS["min_scene_words"]:
            warnings.append(f"Scene {number} narration is only {count} words.")
        elif count > LIMITS["max_scene_words"]:
            issues.append(f"Scene {number} narration is {count} words; a Shorts scene must stay under "
                          f"{LIMITS['max_scene_words']}.")
        if not str(scene.get("image") or "").strip() and not scene.get("keywords"):
            warnings.append(f"Scene {number} has neither a visual prompt nor footage keywords.")
        label = str(scene.get("label") or "")
        if len(label) > 28:
            warnings.append(f"Scene {number} label is {len(label)} characters and may overflow the frame.")
    total_words = sum(scene_words)
    if total_words < LIMITS["min_total_words"]:
        issues.append(f"Narration is only {total_words} words; too short for a Short.")
    elif total_words > LIMITS["max_total_words"]:
        issues.append(f"Narration is {total_words} words (about {total_words / 2.5:.0f}s); "
                      "it would not fit a Short.")

    normalised = [re.sub(r"\W+", " ", text.lower()).strip() for text in spoken if text]
    if len(set(normalised)) < len(normalised):
        issues.append("Two scenes contain identical narration.")

    # Language/script consistency: the voice and caption font follow the language.
    devanagari, latin = _script_mix(" ".join(spoken))
    selected = str(language or "english").strip().lower()
    if selected == "hindi" and devanagari < max(10, latin // 4):
        issues.append("Language is Hindi but the narration is not written in Devanagari.")
    if selected in {"english", "hinglish"} and devanagari > 0:
        issues.append(f"Language is {selected.title()} but the narration contains Devanagari text.")

    everything = " ".join([title, str(script.get("hook") or ""), *spoken]).lower()
    for pattern, description in _RISKY_PATTERNS:
        if re.search(pattern, everything, flags=re.IGNORECASE):
            issues.append(f"Unsafe claim: {description}.")
    category = str(script.get("category") or "").lower()
    if category in {"finance", "markets", "money"} and not re.search(
            r"(not\s+(investment|financial)\s+advice|educational?|education|shiksha|jaankari|"
            r"salah\s+nahi|advice\s+nahi|शैक्षणिक|सलाह\s+नहीं|जानकारी)", everything, flags=re.IGNORECASE):
        warnings.append("Finance script has no spoken educational disclaimer; the on-screen one is still shown.")

    status = "FAIL" if issues else ("REVIEW" if warnings else "PASS")
    return {
        "status": status, "issues": issues, "warnings": warnings,
        "metrics": {"scene_count": len(scenes), "total_words": total_words,
                    "scene_words": scene_words, "language": selected,
                    "estimated_seconds": round(total_words / 2.5, 1)},
    }


def assert_shorts_script(script, language: str = "english") -> dict:
    report = validate_shorts_script(script, language)
    if report["status"] == "FAIL":
        raise ValueError("Shorts script failed content QA: " + "; ".join(report["issues"]))
    return report
