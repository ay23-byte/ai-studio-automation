import json, re, requests, time
from core.config import GEMINI_API_KEY, GEMINI_MODEL

SHORTS_PROMPT = """Write ONE Shorts script for a Hindi/Hinglish YouTube channel about: "{topic}" (category: {category})

RULES:
- "say": Devanagari Hindi only, no English letters or digits, spell numbers in words
- "caption": Roman Hinglish, same number of sentences as "say"
- 5-6 scenes, 10-22 spoken words per scene, 70-110 total words
- Finance topics: education only, no advice, no company names, no promised returns
- Last scene: disclaimer + follow call to action
- "label": 1-3 Roman words max 22 chars
- "image": English AI image prompt (no text, faces, logos, charts with data)
- "keywords": 1-2 English stock footage words

Return ONLY valid JSON:
{{"title":"Roman Hinglish title max 80 chars","hook":"Roman hook question max 50 chars","category":"{category}","scenes":[{{"say":"...","caption":"...","label":"...","image":"...","keywords":["..."]}}]}}"""

LONGFORM_PROMPT = """Write ONE long-form script for a Hindi/Hinglish YouTube channel about: "{topic}"
Category: {category}, Target: {duration_min}-{duration_max} minutes, Language: {language}

Structure: Hook (30s) > Intro (1min) > 3-5 Main Sections (2-3min each) > Summary (1min) > CTA (30s)

Each section needs: "title", "narration" (natural spoken Hinglish), "visuals" (3-5 descriptions),
"image" (AI image prompt, no text/faces/logos), "keywords" (2-3 stock footage words)

Finance: education only, no investment advice, no company names, no promised returns.
Sound like a real person speaking, not an essay.

Return ONLY valid JSON:
{{"title":"...","description":"100 word SEO description","tags":["..."],"hook":"...","category":"{category}","format":"longform","sections":[{{"title":"...","narration":"...","visuals":["..."],"image":"...","keywords":["..."]}}]}}"""

def _ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set. Get a free key at aistudio.google.com")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    for attempt in range(3):
        r = requests.post(url,
            headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0.85, "maxOutputTokens": 4096}},
            timeout=120)
        if r.status_code == 429:
            time.sleep(30 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise RuntimeError("Gemini rate-limited after 3 tries")

def _parse(text: str) -> dict:
    text = text.strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1: raise ValueError("No JSON in response")
    return json.loads(text[a:b+1])

def generate_shorts_script(topic: str, category: str = "finance") -> dict:
    return _parse(_ask_gemini(SHORTS_PROMPT.format(topic=topic, category=category)))

def generate_longform_script(topic: str, category: str = "finance",
                              duration_min=5, duration_max=8, language="Hinglish") -> dict:
    s = _parse(_ask_gemini(LONGFORM_PROMPT.format(
        topic=topic, category=category,
        duration_min=duration_min, duration_max=duration_max, language=language)))
    s["format"] = "longform"
    return s

def generate_seo(script: dict) -> dict:
    title = script.get("title", "")
    cat = script.get("category", "")
    tags = script.get("tags", ["finance", "hinglish", "education"])
    titles = [
        f"{title} | Power Codex",
        f"{title} - Aasaan Hinglish mein",
        f"Kya aap jaante ho? {title}",
        f"{title} | {cat.title()} Explained",
        f"Samjho {title} | Hindi mein",
    ]
    chapters, t = [], 0
    for sec in script.get("sections", []):
        chapters.append(f"{t//60:02d}:{t%60:02d} {sec['title']}")
        t += 120
    return {
        "titles": titles,
        "tags": tags + [cat, "power codex", "hindi education"],
        "chapters": chapters,
        "pinned_comment": "कौन सा concept अगले video में समझाएँ? Comment में बताओ 👇",
        "hashtags": [f"#{t.replace(' ','')}" for t in tags[:5]],
    }
