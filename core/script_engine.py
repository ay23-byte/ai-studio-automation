import json, re, requests, time
from core.config import GEMINI_API_KEY, GEMINI_MODEL
from core.config import GEMINI_FALLBACK_MODEL

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

LONGFORM_PROMPT = """You are an exceptional documentary filmmaker, senior subject researcher, Hindi/Hinglish educator, and retention-focused YouTube writer.

Create a complete, original long-form YouTube documentary/explainer about: "{topic}"
Category: {category}
Target runtime: {duration_min}-{duration_max} minutes
Narration language: {language}

STORY STRUCTURE:
1. Cold open with a surprising question, vivid moment, puzzle, or consequence. No generic greeting.
2. Promise what viewers will understand and why it matters.
3. Establish context with a simple relatable analogy.
4. Build 6-10 logically ordered chapters (adjust to runtime), each adding a new idea.
5. Include concrete examples, thought experiments, comparisons and step-by-step explanations where useful.
6. Use honest open loops and resolve them later. Add natural chapter transitions, not fake hype.
7. Explain uncertainty, limitations and common misconceptions.
8. End by answering the opening question, summarizing the mental model and giving a meaningful CTA.

RUNTIME AND DEPTH:
- Aim for 125-145 spoken words per minute.
- Total narration target is approximately runtime_minutes × 130 words. Never pad with repetition.
- Each chapter must explain its idea fully; normally 120-220 words per chapter depending on target runtime.
- Make this a substantial educational experience, not a short summary stretched across minutes.

NARRATION:
- Write natural spoken Hindi in Devanagari when language is Hindi or Hinglish. Use English only for necessary technical terms and explain them simply.
- Use a confident, warm documentary voice with varied sentence length.
- Explain each technical term at first use. Analogies are aids, not proof.
- Use correct examples and calculations only.
- No headings, bullets, markdown, stage directions or camera instructions inside narration.
- Do not invent statistics, studies, quotes, dates, citations, people or facts. Flag claims needing verification in fact_check_notes.
- Finance: education only, no personalized advice, guarantees or buy/sell recommendations.

VISUAL STORYBOARD:
- Every chapter must contain 3-5 distinct "visuals" entries in chronological order.
- Each is a standalone 16:9 documentary B-roll shot description: subject, action, environment, composition, camera movement and lighting.
- Visuals must illustrate the narration beat, not generic filler. Vary wide shots, close-ups, macro details, diagrams without labels, environments and visual metaphors.
- No readable text, captions, logos, watermarks or recognizable real-person likenesses.
- "image" is a fallback key visual prompt. "keywords" contains 2-4 stock-footage search terms.

Return ONLY valid JSON:
{{"title":"Accurate compelling title","description":"Natural SEO description","tags":["..."],"hook":"Cold-open line","category":"{category}","format":"longform","estimated_minutes":{duration_max},"fact_check_notes":["Claims requiring verification, or empty array"],"sections":[{{"title":"Chapter title","narration":"Complete spoken narration in natural paragraphs","visuals":["Detailed visual shot one","Detailed visual shot two","Detailed visual shot three"],"image":"Fallback key visual prompt","keywords":["search term one","search term two"]}}]}}

No markdown fences or commentary. Ensure the JSON parses."""


def _ask_model(prompt: str, model: str, attempts: int = 4):
    """Call Gemini with exponential backoff for transient API/network failures."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 8192},
    }
    last_error = None

    for attempt in range(attempts):
        try:
            response = requests.post(
                url,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            delay = min(8 * (2 ** attempt), 45)
            print(f"Gemini network error on {model}; retrying in {delay}s ({attempt + 1}/{attempts})")
            time.sleep(delay)
            continue

        if response.status_code == 200:
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates for {model}: {data.get('promptFeedback', data)}")
            parts = candidates[0].get("content", {}).get("parts", [])
            generated = "".join(part.get("text", "") for part in parts if part.get("text"))
            if not generated.strip():
                raise RuntimeError(f"Gemini returned an empty response for {model}")
            return generated

        last_error = f"HTTP {response.status_code}: {response.text[:1000]}"
        retryable = response.status_code in (429, 500, 502, 503, 504)
        if not retryable or attempt == attempts - 1:
            break

        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(8 * (2 ** attempt), 45)
        except ValueError:
            delay = min(8 * (2 ** attempt), 45)
        delay = max(1, min(delay, 60))
        print(
            f"Gemini {response.status_code} from {model}; "
            f"retrying in {int(delay)}s ({attempt + 1}/{attempts})"
        )
        time.sleep(delay)

    raise RuntimeError(f"Gemini request failed for model {model} after {attempts} attempts: {last_error}")


def _ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set. Get a free key at aistudio.google.com")

    try:
        return _ask_model(prompt, GEMINI_MODEL)
    except RuntimeError as primary_error:
        # 503/5xx capacity incidents can affect one model more than another.
        # Try the configured lightweight stable model as a fallback.
        if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
            print(
                f"Primary Gemini model {GEMINI_MODEL} is unavailable. "
                f"Trying fallback model {GEMINI_FALLBACK_MODEL}."
            )
            try:
                return _ask_model(prompt, GEMINI_FALLBACK_MODEL, attempts=3)
            except RuntimeError as fallback_error:
                raise RuntimeError(
                    f"Gemini generation failed on both models. "
                    f"Primary: {primary_error}. Fallback: {fallback_error}"
                ) from fallback_error
        raise


def _parse(text: str) -> dict:
    text = text.strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1: raise ValueError("No JSON in response")
    return json.loads(text[a:b+1])


def _apply_channel_voice(prompt: str, channel_name: str = "",
                         audience: str = "", brand_voice: str = "") -> str:
    context = []
    if channel_name:
        context.append(f"Channel brand: {channel_name}")
    if audience:
        context.append(f"Target audience: {audience}")
    if brand_voice:
        context.append(f"Channel-specific brand voice and editorial rules:\n{brand_voice[:3000]}")
    if not context:
        return prompt
    return prompt + "\n\nCHANNEL PROFILE (follow these rules consistently):\n" + "\n".join(context) + """
Additional channel rules:
- Keep this channel's identity distinct; do not mention another channel's name.
- Follow the specified language, audience level and tone.
- Do not invent statistics, sources, citations or current trends.
- Preserve all safety and factuality rules in the base prompt.
"""


def generate_shorts_script(topic: str, category: str = "finance",
                           channel_name: str = "", audience: str = "",
                           brand_voice: str = "") -> dict:
    prompt = SHORTS_PROMPT.format(topic=topic, category=category)
    prompt = _apply_channel_voice(prompt, channel_name, audience, brand_voice)
    script = _parse(_ask_gemini(prompt))
    script["format"] = "shorts"
    script["category"] = category
    return script


def generate_longform_script(topic: str, category: str = "finance",
                              duration_min=5, duration_max=8, language="Hinglish",
                              channel_name: str = "", audience: str = "",
                              brand_voice: str = "") -> dict:
    prompt = LONGFORM_PROMPT.format(
        topic=topic, category=category,
        duration_min=duration_min, duration_max=duration_max, language=language)
    prompt = _apply_channel_voice(prompt, channel_name, audience, brand_voice)
    script = _parse(_ask_gemini(prompt))
    script["format"] = "longform"
    script["category"] = category
    return script


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
