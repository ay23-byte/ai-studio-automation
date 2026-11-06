import json, re, requests, time, random
from core.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS
from core.news_research import research_topic, format_research_context

class GeminiRequestError(RuntimeError):
    """Gemini API failure with metadata for safe model failover."""
    def __init__(self, model, message, status_code=None, retryable=False):
        super().__init__(message)
        self.model = model
        self.status_code = status_code
        self.retryable = retryable

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
- Every chapter must contain 3-5 distinct "visuals" objects in chronological order, each tied to the exact spoken narration.
- Each visual object must contain "narration_anchor" (an exact short phrase copied verbatim from this chapter's narration), "visual_prompt" (a standalone 16:9 documentary shot description), and "keywords" (2-4 precise stock-footage search terms for that exact moment).
- The narration_anchor phrases must appear verbatim in the narration, in the same order, and be spread across the chapter from beginning to end.
- The visual_prompt must depict the specific subject, action, concept, place or process explained at that anchor. Do not use generic space, city, technology or people footage unless the narration is actually about those things.
- For scientific explanations, depict the actual object/process being discussed; if literal depiction is difficult, use a clearly relevant scientific visualization or physical analogy, never unrelated cinematic filler.
- Vary wide shots, close-ups, macro details, accurate unlabeled diagrams and relevant environments.
- No readable text, captions, logos, watermarks or recognizable real-person likenesses.
- "image" is a fallback key visual prompt. "keywords" contains 2-4 chapter-level backup terms.

Return ONLY valid JSON:
{{"title":"Accurate compelling title","description":"Natural SEO description","tags":["..."],"hook":"Cold-open line","category":"{category}","format":"longform","estimated_minutes":{duration_max},"fact_check_notes":["Claims requiring verification, or empty array"],"sections":[{{"title":"Chapter title","narration":"Complete spoken narration in natural paragraphs","visuals":[{{"narration_anchor":"Exact short phrase copied verbatim from this narration","visual_prompt":"Specific 16:9 visual that illustrates the exact narration at this phrase","keywords":["precise stock search term one","precise stock search term two"]}},{{"narration_anchor":"Another exact later phrase copied from this narration","visual_prompt":"A different visual accurately illustrating that later narration moment","keywords":["specific search term"]}},{{"narration_anchor":"A third exact phrase from later in the narration","visual_prompt":"A third specific matching visual","keywords":["specific search term"]}}],"image":"Fallback key visual prompt","keywords":["chapter backup term one","chapter backup term two"]}}]}}

No markdown fences or commentary. Ensure the JSON parses."""


def _ask_model(prompt: str, model: str, attempts: int = 4):
    """Call Gemini with bounded exponential backoff for transient failures."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # Ask Gemini for JSON-only output and allow enough room for a full
        # multi-chapter storyboard (narration plus visual prompts).
        "generationConfig": {
            "temperature": 0.65,
            "maxOutputTokens": 16384,
            "responseMimeType": "application/json",
        },
    }
    for attempt in range(attempts):
        try:
            response = requests.post(
                url,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
        except requests.RequestException as exc:
            if attempt == attempts - 1:
                raise GeminiRequestError(model, f"Network failure calling {model}: {exc}",
                                         retryable=True) from exc
            delay = min(10 * (2 ** attempt), 60) + random.uniform(0, 2)
            print(f"Gemini network error on {model}; retrying in {delay:.0f}s ({attempt + 1}/{attempts})")
            time.sleep(delay)
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise GeminiRequestError(model, f"Gemini returned invalid JSON for {model}.",
                                         status_code=200, retryable=True) from exc
            candidates = data.get("candidates") or []
            if not candidates:
                raise GeminiRequestError(
                    model, f"Gemini returned no candidates for {model}: "
                    f"{data.get('promptFeedback', data)}",
                    status_code=200, retryable=False)
            candidate = candidates[0]
            finish_reason = str(candidate.get("finishReason") or "").upper()
            if finish_reason == "MAX_TOKENS":
                raise GeminiRequestError(
                    model,
                    f"Gemini stopped generation at the output-token limit for {model}; "
                    "the storyboard may be incomplete. Trying another configured model.",
                    status_code=200,
                    retryable=True,
                )
            if finish_reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
                raise GeminiRequestError(
                    model,
                    f"Gemini blocked the response for {model} (finishReason={finish_reason}). "
                    "Review the topic and prompt.",
                    status_code=200,
                    retryable=False,
                )
            parts = candidate.get("content", {}).get("parts", [])
            generated = "".join(part.get("text", "") for part in parts if part.get("text"))
            if not generated.strip():
                raise GeminiRequestError(model, f"Gemini returned an empty response for {model}.",
                                         status_code=200, retryable=True)
            return generated

        last_error = f"HTTP {response.status_code}: {response.text[:1000]}"
        retryable = response.status_code in (408, 409, 425, 429, 500, 502, 503, 504)
        if not retryable:
            raise GeminiRequestError(
                model,
                f"Gemini rejected model {model}: {last_error}. "
                "Check the model name, API key, and Google AI Studio project access.",
                status_code=response.status_code, retryable=False)

        if attempt == attempts - 1:
            raise GeminiRequestError(
                model, f"Gemini request failed for {model} after {attempts} attempts: {last_error}",
                status_code=response.status_code, retryable=True)

        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else min(10 * (2 ** attempt), 60)
        except (TypeError, ValueError):
            delay = min(10 * (2 ** attempt), 60)
        delay = max(1, min(delay, 90)) + random.uniform(0, 2)
        print(f"Gemini HTTP {response.status_code} from {model}; "
              f"retrying in {delay:.0f}s ({attempt + 1}/{attempts})")
        time.sleep(delay)

    raise GeminiRequestError(model, f"Gemini request failed for {model}.", retryable=True)


def _ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to GitHub Actions secrets.")

    models = []
    for model in [GEMINI_MODEL, *GEMINI_FALLBACK_MODELS]:
        model = str(model or "").strip()
        if model and model not in models:
            models.append(model)
    if not models:
        raise RuntimeError("No Gemini model is configured. Set GEMINI_MODEL.")

    failures = []
    for index, model in enumerate(models):
        try:
            print(f"Requesting script from Gemini model: {model}")
            return _ask_model(prompt, model, attempts=4)
        except GeminiRequestError as exc:
            failures.append(f"{model}: {exc}")
            # A 404 generally means this model ID is retired, unavailable to this
            # API project, or not exposed to this account. Try the next configured
            # model instead of aborting the entire script-generation workflow.
            if exc.status_code == 404:
                if index < len(models) - 1:
                    print(f"Model {model} is not available (HTTP 404). Skipping to next fallback.")
                continue
            if not exc.retryable:
                raise RuntimeError(str(exc)) from exc
            if index < len(models) - 1:
                print(f"Model {model} is temporarily unavailable. Trying next fallback.")

    details = "\n".join(f"- {item}" for item in failures)
    raise RuntimeError(
        "Gemini is temporarily unavailable across all configured models. "
        "No video was built. Wait a few minutes and start a NEW workflow run. "
        "If this continues, check Google AI Studio service status and API quota.\n"
        + details
    )


def _parse(text: str) -> dict:
    """Parse Gemini JSON defensively without silently accepting incomplete scripts."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Gemini returned an empty script response.")
    cleaned = text.strip()
    # Remove optional Markdown fences even though the prompt requests raw JSON.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("Gemini response did not contain a JSON object.")
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as first_error:
        # Some model responses contain harmless trailing commas. Normalize only
        # commas immediately before a closing brace/bracket, then parse again.
        candidate = cleaned[start:]
        candidate = re.sub(r",\s*}", "}", candidate)
        candidate = re.sub(r",\s*]", "]", candidate)
        try:
            parsed, end = decoder.raw_decode(candidate)
        except json.JSONDecodeError as final_error:
            excerpt = candidate[max(0, final_error.pos - 120):final_error.pos + 120]
            raise ValueError(
                "Gemini returned malformed or truncated JSON; the script was not "
                "passed to rendering. Retry generation after the API quota recovers. "
                f"Parser location: line {final_error.lineno}, column {final_error.colno}. "
                f"Response excerpt: {excerpt!r}"
            ) from first_error
    if not isinstance(parsed, dict):
        raise ValueError("Gemini script response must be a JSON object.")
    return parsed


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


def validate_longform_storyboard(script: dict) -> dict:
    """Reject long-form storyboards whose visual anchors do not map to narration."""
    sections = script.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("Long-form script must contain a non-empty sections list.")
    for section_index, section in enumerate(sections, start=1):
        narration = str(section.get("narration") or "").strip()
        visuals = section.get("visuals")
        if not narration:
            raise ValueError(f"Chapter {section_index} has no narration.")
        if not isinstance(visuals, list) or len(visuals) < 3:
            raise ValueError(f"Chapter {section_index} needs at least 3 narration-linked visual beats.")
        search_from = 0
        for beat_index, beat in enumerate(visuals, start=1):
            if not isinstance(beat, dict):
                raise ValueError(f"Chapter {section_index}, visual {beat_index} must be a structured object.")
            anchor = str(beat.get("narration_anchor") or "").strip()
            visual_prompt = str(beat.get("visual_prompt") or "").strip()
            keywords = beat.get("keywords")
            if not anchor or not visual_prompt or not isinstance(keywords, list) or not any(str(k).strip() for k in keywords):
                raise ValueError(f"Chapter {section_index}, visual {beat_index} is missing anchor, visual prompt or stock keywords.")
            position = narration.casefold().find(anchor.casefold(), search_from)
            if position < 0:
                raise ValueError(f"Chapter {section_index}, visual {beat_index} anchor is absent or out of order. Regenerate with exact copied phrases.")
            search_from = position + len(anchor)
    return script

def _repair_longform_visual_anchors(script: dict) -> dict:
    """Guarantee every visual anchor is an exact, chronological narration excerpt.

    Models sometimes paraphrase anchors or place them out of order. If any
    anchor fails the same exact check used by the validator, rebuild the whole
    chapter's anchor sequence from non-overlapping word ranges in narration.
    Visual prompts and search keywords are deliberately left unchanged.
    """
    import re

    for section_index, section in enumerate(script.get("sections") or [], start=1):
        narration = str(section.get("narration") or "").strip()
        visuals = section.get("visuals")
        if not narration or not isinstance(visuals, list) or not visuals:
            continue

        # First preserve a storyboard only if every anchor is an exact,
        # forward-moving substring (same rule as validate_longform_storyboard).
        cursor = 0
        valid = True
        for beat in visuals:
            if not isinstance(beat, dict):
                valid = False
                break
            anchor = str(beat.get("narration_anchor") or "").strip()
            position = narration.casefold().find(anchor.casefold(), cursor) if anchor else -1
            if position < 0:
                valid = False
                break
            cursor = position + len(anchor)

        if valid:
            continue

        words = list(re.finditer(r"\\S+", narration))
        if len(words) < len(visuals):
            raise ValueError(
                f"Chapter {section_index} has fewer narration words than visual beats; "
                "cannot create distinct chronological anchors."
            )

        # Split the narration into disjoint, chronological word ranges.
        # A short exact excerpt from each range guarantees ordered anchors.
        count = len(visuals)
        for beat_index, beat in enumerate(visuals):
            if not isinstance(beat, dict):
                raise ValueError(
                    f"Chapter {section_index}, visual {beat_index + 1} must be a structured object."
                )
            lo = (beat_index * len(words)) // count
            hi = ((beat_index + 1) * len(words)) // count
            hi = max(lo + 1, hi)
            phrase_words = min(8, hi - lo)
            # Keep the anchor near the centre of its own range while retaining
            # exact original punctuation and whitespace from the narration.
            anchor_start = lo + max(0, (hi - lo - phrase_words) // 2)
            anchor_end = anchor_start + phrase_words
            beat["narration_anchor"] = narration[
                words[anchor_start].start():words[anchor_end - 1].end()
            ]

        print(
            f"WARNING: Chapter {section_index} had inaccurate/out-of-order visual anchors. "
            "Replaced all anchors with exact chronological narration excerpts; "
            "visual prompt relevance still requires editorial review."
        )
    return script


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

    # Free, keyless GDELT discovery adds recent-news leads to both current
    # affairs and evergreen documentaries. Failure is deliberately non-fatal.
    research_sources = research_topic(topic)
    prompt += "\n\n" + format_research_context(research_sources)
    prompt = _apply_channel_voice(prompt, channel_name, audience, brand_voice)
    script = _parse(_ask_gemini(prompt))
    script["format"] = "longform"
    script["category"] = category
    script["research_sources"] = research_sources
    script = _repair_longform_visual_anchors(script)
    return validate_longform_storyboard(script)


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


# Record script-generation outcome without storing prompt text.
from core.monitoring import monitored as _monitored
_ask_gemini = _monitored("gemini", "generate_script", _ask_gemini)
