import json, re, requests, time, random
from core.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS, CHANNEL_NAME
from core.news_research import research_topic, format_research_context

class GeminiRequestError(RuntimeError):
    """Gemini API failure with metadata for safe model failover."""
    def __init__(self, model, message, status_code=None, retryable=False):
        super().__init__(message)
        self.model = model
        self.status_code = status_code
        self.retryable = retryable

SHORTS_PROMPT = """Write ONE polished English-language YouTube Shorts script for the channel "{channel_name}" about: "{topic}" (category: {category}).

LANGUAGE: English only. Every spoken line, caption, title, hook, label and on-screen phrase must be natural English in Latin script. Never use Hindi, Hinglish, Devanagari or transliterated Hindi.

RULES:
- 5-6 scenes, 10-22 spoken words per scene, 70-110 total spoken words
- "say" is the exact English narration; "caption" is concise English subtitle text matching the spoken meaning
- Finance topics: education only, no personal advice, no promised returns
- Last scene: concise educational disclaimer + follow call to action
- "label": 1-3 English words, max 22 characters
- "image": specific English cinematic visual prompt (no text, logos or invented factual imagery)
- "keywords": 2-4 specific English footage search terms relevant to the scene
- Avoid generic repeated topics and generic stock imagery; each scene must have a distinct visual idea

Return ONLY valid JSON:
{{"title":"English title max 80 chars","hook":"English hook max 50 chars","category":"{category}","scenes":[{{"say":"...","caption":"...","label":"...","image":"...","keywords":["..."]}}]}}"""

LONGFORM_PROMPT = """You are the showrunner, investigative journalist, fact-checking researcher, documentary scriptwriter and cinematic visual director for a premium YouTube documentary.

Create a rigorous, emotionally engaging investigative documentary about: "{topic}"
Category: {category}
Target runtime: {duration_min}-{duration_max} minutes
Narration language: {language}

EDITORIAL IDENTITY:
- First-person host-led investigation: curious, observant, skeptical, fair and conversational.
- The host explains what they investigated, what the evidence shows, what remains uncertain and why it matters.
- Use a cinematic investigative tone: restrained tension, precise reveals, human consequences and clear explanations. No fake urgency, conspiracy framing or unsupported accusations.
- The documentary should feel like a carefully researched premium video essay, not a generic classroom lecture or listicle.

REQUIRED STORY ARC:
1. COLD OPEN (first 15-25 seconds): open on a concrete contradiction, surprising verified fact, unresolved question or consequence. Do not greet the audience.
2. TITLE/PROMISE: state the central question and what the investigation will establish.
3. CHAPTER 1 — ORIGINS & CONTEXT: explain how the subject emerged, key actors, timeline and the system around it.
4. CHAPTER 2 — NAMED CASE STUDY: investigate one specific, well-documented case, organization, event, person or place. Explain what happened and what primary/credible evidence establishes it.
5. CHAPTER 3 — HOW THE SYSTEM/MONEY WORKS: explain the mechanism step by step. If financial or quantitative data exists, include a transparent worked calculation with inputs, units, formula, source and assumptions. If not, explain the mechanism qualitatively; never invent example data and pass it off as real.
6. CHAPTER 4 — EXPERT PERSPECTIVES: create two clearly labelled expert-perspective segments with useful questions and the evidence or published positions that can be attributed. Do NOT invent an interview, quote, answer, endorsement, voice, footage or meeting. If no recorded interview/transcript is supplied, write these as "questions I would put to an expert" plus a careful summary of attributable published research—not as a real interview.
7. CHAPTER 5 — PEOPLE, COSTS & CONSEQUENCES: show implications for ordinary people, smaller creators/businesses/workers/communities or other directly affected groups, plus realistic barriers and entry costs where relevant. Do not force creator economics into unrelated topics.
8. OUTRO: resolve the opening question, distinguish what is known from what remains open, and end with a memorable but proportionate takeaway and CTA.

Adapt chapter content to the topic; retain the five-chapter investigative spine where relevant. For topics where a section is genuinely irrelevant, replace it with a topic-appropriate investigation and explain the editorial choice in fact_check_notes.

RESEARCH & EVIDENCE RULES:
- Use the supplied research context as leads, not unquestionable truth. Prefer primary sources, official datasets, court/regulatory filings, peer-reviewed research, company filings and direct institutional publications; use reputable journalism for context.
- Never invent statistics, dates, names, quotes, interviews, documents, studies, URLs, source titles or causal claims.
- Every material factual/numeric claim must be traceable to one or more source records. Include source title, publisher, publication date (if known), URL and claim supported. If publication date is unavailable, say so.
- Include an exhaustive top-level claims array for every material factual/numeric assertion in the narration. Each claim needs claim_id, exact concise claim text, claim_type (fact, estimate, allegation or analysis), source_ids that refer to the sources array, and an evidence note identifying the relevant source passage/table/dataset. If no source supports it, use an empty source_ids array and state that it is unverified; never invent evidence.
- Do not label any claim as verified. Verification is an editorial decision made only after a human opens and inspects the original source.
- Distinguish measured fact, attributed claim, estimate, inference and allegation. Attribute allegations and include relevant response/context when available.
- For calculations, show formula, source values, units, assumptions and result; mark illustrative calculations as illustrative and never disguise assumptions as observed facts.
- Put unsupported or disputed claims in fact_check_notes and avoid presenting them as established narration.
- No defamatory or accusatory language beyond what reliable evidence supports. Explain uncertainty and limitations.

RUNTIME & NARRATION:
- Aim for approximately 145-155 spoken words per minute; total narration should fit the selected runtime, not include visual directions.
- The requested upper runtime is {duration_max} minutes. Write at least {minimum_words} spoken words across all section narration (excluding titles, visual directions, captions and metadata), and target approximately {target_words} words.
- Distribute narration meaningfully across the cold open, five chapters and outro. Do not make chapters into short summaries.
- Do not pad or repeat. Expand with sourced context, mechanisms, examples, evidence limitations and consequences. Never invent facts to reach a word count.
- Write natural spoken narration in the requested language. For Hindi/Hinglish, use conversational Devanagari Hindi with necessary English technical terms explained at first use.
- First-person host narration is welcome ("मैंने इस रिपोर्ट में...", "जब मैंने आंकड़े देखे...") only when it describes the research process, not a fictional real-world visit, call, interview or firsthand observation.
- No headings, bullets, markdown, camera directions or production notes inside narration.
- Vary sentence length, use transitions and resolve open loops.

CINEMATIC VISUAL STORYBOARD:
- Each chapter should contain 4-5 structured visual beats in chronological order (use 6-8 only for genuinely short chapters). Each beat needs an exact short narration_anchor copied verbatim from that chapter's narration, a distinct visual_prompt, and 2-4 precise stock keywords.
- Spread anchors evenly from the beginning to the end of the chapter. They must be exact substrings in order. Do not reuse the same subject, framing or stock-search idea for adjacent beats.
- Aim for a purposeful new visual idea at meaningful narration transitions. Mix establishing wides, medium action, close-up details, environmental cutaways, process shots, maps, diagrams and evidence graphics. Avoid filler, repetitive stock and arbitrary fast cuts.
- Include visual_type from: stock, archival, ai_reconstruction, diagram, map, chart, timeline, document, person, location, interview, presenter.
- Include subject, location, time_period, shot_size, camera_movement, search_queries (2-5), and visual_guardrail where useful.
- Prefer real scene-matched stock footage, verified archival material, accurate maps, period-specific art, document close-ups, macro details, environmental wides, process shots, evidence boards and purposeful presenter moments.
- For charts, provide chart_spec with chart_title, unit, labels, values, source_refs and notes. Only populate numeric values when supported by cited research. Otherwise request a qualitative graphic with no fabricated data.
- For timelines, provide timeline_events with date, event and source_refs. Never invent dates.
- For maps, specify named locations and the relevant historical/current boundary context; do not invent borders.
- For documents, use only verified source material; never create fake readable headlines, court records, contracts, screenshots or quotations.
- Archival material must be labelled archival only when its provenance is verified. Before photography, prefer period paintings, engravings, maps, artifacts or a clearly disclosed AI reconstruction.
- AI reconstructions must be disclosed and must never impersonate real people or be passed off as documentary evidence.
- Interview scenes must use supplied genuine recordings only. Without supplied recordings, render a question card, research note or presenter narration—not a fake talking expert.
- No celebrity likenesses, fake logos, watermarks or fabricated legible text in generated imagery.
- Use varied wide, medium, close-up and macro compositions; camera movement should support the information rather than decorate it.
- Visual prompts must depict the exact narration moment. No generic city/technology/space filler unless the narration is actually about it.

SOUND & EDITORIAL:
- Suggest restrained cinematic sound design: low room tone, subtle impacts, paper handling, mechanical ambience, risers only at genuine transitions, and music that leaves narration intelligible.
- Do not use constant trailer booms or manipulate viewers with alarmist sound.
- Give every chapter a distinct visual/emotional progression while maintaining a coherent warm, dark, investigative palette with restrained deep red and amber accents.
- On-screen text must be short, accurate and sourced. Numeric claims need a visible source cue in the scene metadata.

OUTPUT:
Return ONLY valid JSON with this structure:
{{"title":"Accurate compelling title","description":"Natural SEO description","tags":["..."],"hook":"Cold-open line","category":"{category}","format":"longform","estimated_minutes":{duration_max},"editorial_style":"first_person_investigative","fact_check_notes":["..."],"sources":[{{"source_id":"S1","title":"Source title","publisher":"Publisher","published_at":"date or unknown","url":"https://...","supports":["specific claim"]}}],"claims":[{{"claim_id":"C1","text":"One concise checkable factual claim","claim_type":"fact|estimate|allegation|analysis","source_ids":["S1"],"evidence":"Relevant source passage, table, dataset or section; paraphrase only"}}],"sections":[{{"title":"Chapter title","section_type":"cold_open|context|case_study|mechanism|expert_perspective|consequences|outro","narration":"Complete spoken narration in natural paragraphs","visuals":[{{"narration_anchor":"Exact short phrase copied verbatim from this narration","visual_prompt":"Specific cinematic 16:9 shot depicting this exact narration moment","visual_type":"stock","subject":"Specific subject","location":"Named location or unknown","time_period":"Supported period or unknown","shot_size":"close-up, medium, wide or macro","camera_movement":"subtle push-in, tracking, locked-off, etc.","keywords":["precise stock term one","precise stock term two"],"search_queries":["specific search phrase one","specific search phrase two"],"source_refs":["S1"],"chart_spec":null,"timeline_events":[],"on_screen_text":"Short sourced text or empty string","sound_design":"Subtle scene-specific sound cue","visual_guardrail":"Evidence and authenticity constraints"}}],"image":"Fallback key visual prompt","keywords":["chapter backup term one","chapter backup term two"]}}]}}

Return raw JSON only. No markdown fences or commentary. Ensure valid JSON and complete narration. Do not invent missing evidence to satisfy the requested structure."""


def _ask_model(prompt: str, model: str, attempts: int = 3):
    """Call Gemini with bounded exponential backoff for transient failures."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # Ask Gemini for JSON-only output and allow enough room for a full
        # multi-chapter storyboard (narration plus visual prompts).
        "generationConfig": {
            "temperature": 0.45,
            "maxOutputTokens": 32768,
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
            delay = min(5 * (2 ** attempt), 30) + random.uniform(0, 1)
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
            delay = float(retry_after) if retry_after else min(5 * (2 ** attempt), 30)
        except (TypeError, ValueError):
            delay = min(5 * (2 ** attempt), 30)
        delay = max(1, min(delay, 45)) + random.uniform(0, 1)
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
            return _ask_model(prompt, model, attempts=3)
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
        # Repair bare source IDs such as [S1] into valid JSON string values.
        candidate = re.sub(r'(?<=[,\[\s])(S\d+)(?=\s*[,\]])', r'"\1"', candidate)
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
        if not narration:
            continue

        if not isinstance(visuals, list):
            visuals = []
            section["visuals"] = visuals

        # A model occasionally omits storyboard beats for a short closing
        # chapter. Add neutral presenter/typography beats anchored to the
        # actual narration rather than failing the entire documentary or
        # inventing archival footage, locations, people, or evidence.
        if len(visuals) < 3:
            words = list(re.finditer(r"\S+", narration))
            if len(words) < 3:
                raise ValueError(
                    f"Chapter {section_index} has too little narration to create "
                    "three distinct visual beats."
                )
            original_count = len(visuals)
            while len(visuals) < 3:
                beat_index = len(visuals)
                lo = (beat_index * len(words)) // 3
                hi = ((beat_index + 1) * len(words)) // 3
                hi = max(lo + 1, hi)
                anchor_end = min(hi, lo + 8)
                anchor = narration[words[lo].start():words[anchor_end - 1].end()]
                visuals.append({
                    "narration_anchor": anchor,
                    "visual_prompt": (
                        "Restrained documentary presenter shot, visually emphasizing "
                        f"the narration: {anchor}. Use a clean, cinematic composition; "
                        "do not depict an unverified event, person, place, document, "
                        "or statistic."
                    ),
                    "visual_type": "presenter",
                    "subject": "Documentary presenter and on-screen narration emphasis",
                    "location": "studio",
                    "time_period": "present day",
                    "shot_size": "medium" if beat_index % 2 == 0 else "close-up",
                    "camera_movement": "subtle push-in",
                    "keywords": ["documentary presenter", "cinematic narration"],
                    "search_queries": ["documentary presenter studio", "cinematic presenter close up"],
                    "source_refs": [],
                    "chart_spec": None,
                    "timeline_events": [],
                    "on_screen_text": "",
                    "sound_design": "Subtle room tone under narration",
                    "visual_guardrail": "Presenter/typography only; do not imply unverified evidence.",
                })
            print(
                f"WARNING: Chapter {section_index} contained {original_count} visual beat(s). "
                "Added neutral narration-anchored presenter beats to meet the three-beat minimum."
            )

        if not visuals:
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

        words = list(re.finditer(r"\S+", narration))
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
    prompt = SHORTS_PROMPT.format(topic=topic, category=category, channel_name=channel_name or CHANNEL_NAME)
    prompt = _apply_channel_voice(prompt, channel_name, audience, brand_voice)
    script = _parse(_ask_gemini(prompt))
    script["format"] = "shorts"
    script["category"] = category
    return script


def count_longform_narration_words(script: dict) -> int:
    """Count spoken words only; section titles and visual metadata are excluded."""
    sections = script.get("sections") if isinstance(script, dict) else None
    if not isinstance(sections, list):
        return 0
    narration = " ".join(
        str(section.get("narration") or "")
        for section in sections if isinstance(section, dict)
    )
    # Whitespace tokenization preserves Devanagari combining marks and hyphenated
    # compounds, matching the narration's spoken token boundaries.
    return len(narration.split())


def validate_longform_runtime(script: dict, target_minutes: float,
                              minimum_fraction: float = 0.90,
                              words_per_minute: int = 150) -> dict:
    """Fail before rendering when narration cannot meet the release duration floor."""
    if target_minutes <= 0 or not 0 < minimum_fraction <= 1 or words_per_minute <= 0:
        raise ValueError("Runtime validation requires positive minutes/WPM and a fraction in (0, 1].")
    actual_words = count_longform_narration_words(script)
    minimum_words = int(target_minutes * words_per_minute * minimum_fraction)
    estimated_minutes = actual_words / words_per_minute
    if actual_words < minimum_words:
        raise ValueError(
            f"Long-form narration is too short: {actual_words} spoken words "
            f"(about {estimated_minutes:.1f} min at {words_per_minute} WPM); "
            f"need at least {minimum_words} words to satisfy {minimum_fraction:.0%} "
            f"of the {target_minutes:g}-minute target. Refusing to render an under-length documentary."
        )
    script["narration_word_count"] = actual_words
    script["estimated_narration_minutes"] = round(estimated_minutes, 2)
    script["runtime_validation"] = {
        "target_minutes": target_minutes, "minimum_fraction": minimum_fraction,
        "minimum_words": minimum_words, "actual_words": actual_words,
        "words_per_minute": words_per_minute, "estimated_minutes": round(estimated_minutes, 2),
    }
    return script



def _cap_narration_addition(text: str, word_budget: int) -> str:
    """Keep generated additions within the remaining runtime budget at sentence boundaries."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if word_budget <= 0 or not clean:
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?।])\s+", clean) if part.strip()]
    accepted = []
    used = 0
    for sentence in sentences:
        count = len(sentence.split())
        if used + count <= word_budget:
            accepted.append(sentence)
            used += count
            continue
        remaining = word_budget - used
        if remaining > 0 and not accepted:
            words = sentence.split()[:remaining]
            if words:
                accepted.append(" ".join(words).rstrip(" ,;:") + ".")
        break
    return " ".join(accepted).strip()


def _expand_longform_narration(script: dict, topic: str, category: str,
                               language: str, target_words: int,
                               minimum_words: int, max_passes: int = 2,
                               max_words: int = None) -> dict:
    """Expand only under-length narration, without regenerating the full storyboard.

    The expansion stage is deliberately narration-only: it keeps the expensive
    visual/source JSON out of the response and forbids adding unsupported facts.
    The original runtime gate remains authoritative.
    """
    from copy import deepcopy

    expanded = deepcopy(script)
    # A small tolerance above the requested runtime prevents model over-expansion
    # from turning a 12-minute documentary into a 20-minute TTS job.
    if max_words is None:
        max_words = max(minimum_words, target_words + max(100, int(target_words * 0.08)))
    for pass_index in range(max_passes):
        current_words = count_longform_narration_words(expanded)
        if current_words >= target_words:
            return expanded
        missing = target_words - current_words
        remaining_budget = max_words - current_words
        if remaining_budget <= 0:
            break
        sections = expanded.get("sections") or []
        if not sections:
            raise ValueError("Cannot expand long-form narration: the script has no sections.")

        section_payload = []
        for index, section in enumerate(sections):
            narration = str(section.get("narration") or "").strip()
            section_payload.append({
                "section_index": index,
                "title": str(section.get("title") or f"Section {index + 1}"),
                "section_type": str(section.get("section_type") or "context"),
                "current_word_count": len(narration.split()),
                "narration": narration,
            })

        evidence_payload = {
            "sources": expanded.get("sources") or [],
            "claims": expanded.get("claims") or [],
            "fact_check_notes": expanded.get("fact_check_notes") or [],
        }
        prompt = (
            "You are expanding an existing documentary narration because its runtime is too short. "
            "Return ONLY valid JSON in this exact shape: "
            '{"section_additions":[{"section_index":0,"additional_narration":"..."}]}. '
            f"Topic: {topic}. Category: {category}. Narration language: {language}. "
            f"Current narration: {current_words} words. Required minimum: {minimum_words}. "
            f"Target: {target_words}; hard upper limit: {max_words}. Add approximately {missing} "
            f"new spoken words, and never add more than {remaining_budget} words in this pass. "
            "Distribute additions naturally across the existing sections. "
            "Use only the facts, chronology, people, technical explanations and source-supported "
            "claims already present in the supplied script/evidence. Do not introduce new dates, "
            "measurements, named people, quotations, mission events, statistics, or other checkable "
            "facts that are not already supported there. Do not invent interviews or first-hand "
            "experiences. Expand explanations of already-mentioned engineering choices, constraints, "
            "cause-and-effect reasoning, stakes, uncertainty and why established details matter. "
            "Every addition must be meaningful, specific to this topic, natural to speak aloud, and "
            "must not repeat existing narration. No headings, bullets, stage directions or visual "
            "instructions inside narration. Additions will be appended to the matching section. "
            "Do not return existing narration. Include only sections that need additional material. "
            "If the supplied evidence is insufficient to add the required length without inventing "
            "facts, return the honest amount you can support rather than fabricating material.\n\n"
            "EXISTING SECTIONS:\n" + json.dumps(section_payload, ensure_ascii=False) +
            "\n\nEXISTING EVIDENCE:\n" + json.dumps(evidence_payload, ensure_ascii=False)
        )
        generated = _parse(_ask_gemini(prompt))
        additions = generated.get("section_additions")
        if not isinstance(additions, list):
            raise ValueError("Narration expansion response is missing section_additions.")
        added_words = 0
        seen = set()
        for item in additions:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("section_index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(sections) or index in seen:
                continue
            text = str(item.get("additional_narration") or "").strip()
            text = _cap_narration_addition(text, remaining_budget)
            if len(text.split()) < 20:
                continue
            seen.add(index)
            accepted_words = len(text.split())
            remaining_budget -= accepted_words
            sections[index]["narration"] = (
                str(sections[index].get("narration") or "").rstrip() + "\n\n" + text
            ).strip()
            added_words += accepted_words
            if remaining_budget <= 0:
                break

        expanded["sections"] = sections
        expanded = _repair_longform_visual_anchors(expanded)
        expanded = validate_longform_storyboard(expanded)
        current_words = count_longform_narration_words(expanded)
        print(
            f"Narration-only expansion pass {pass_index + 1}/{max_passes}: "
            f"added {added_words} words; total {current_words}/{minimum_words} minimum "
            f"({target_words} target)."
        )
        if current_words >= minimum_words:
            return expanded
        if added_words == 0:
            break

    raise ValueError(
        f"Long-form narration remains too short after chapter expansion: "
        f"{count_longform_narration_words(expanded)} spoken words; need at least "
        f"{minimum_words}. Refusing to render rather than padding or inventing facts."
    )


def generate_longform_script(topic: str, category: str = "finance",
                              duration_min=5, duration_max=8, language="Hinglish",
                              channel_name: str = "", audience: str = "",
                              brand_voice: str = "") -> dict:
    minimum_words = int(duration_max * 150 * 0.90)
    target_words = int(duration_max * 150)
    prompt = LONGFORM_PROMPT.format(
        topic=topic, category=category, duration_min=duration_min,
        duration_max=duration_max, language=language,
        minimum_words=minimum_words, target_words=target_words)
    # Free, keyless GDELT discovery adds recent-news leads to both current
    # affairs and evergreen documentaries. Failure is deliberately non-fatal.
    research_sources = research_topic(topic)
    prompt += "\n\n" + format_research_context(research_sources)
    prompt = _apply_channel_voice(prompt, channel_name, audience, brand_voice)
    # A short first draft gets up to two evidence-led rewrites. Each rewrite
    # targets a safety margin above the hard runtime floor so small model
    # under-runs do not waste the entire production attempt.
    last_generation_error = None
    last_runtime_error = None
    for attempt in range(3):
        try:
            generated = _ask_gemini(prompt)
            last_generation_error = None
        except RuntimeError as exc:
            # Only retry the aggregate transient-outage error. Authentication,
            # permission and other permanent configuration failures must fail fast.
            if "Gemini is temporarily unavailable across all configured models." not in str(exc):
                raise
            last_generation_error = exc
            if attempt == 2:
                raise
            print(
                f"Gemini generation attempt {attempt + 1}/3 was unavailable; "
                "retrying the script stage with the configured model fallback chain."
            )
            continue

        script = _parse(generated)
        script["format"] = "longform"
        script["category"] = category
        script["research_sources"] = research_sources
        from core.research_verification import append_source_citations_to_description
        script = append_source_citations_to_description(script)
        script = _repair_longform_visual_anchors(script)
        script = validate_longform_storyboard(script)
        try:
            script = validate_longform_runtime(script, duration_max)
            print(
                f"Narration runtime gate passed: {script['narration_word_count']} words, "
                f"approximately {script['estimated_narration_minutes']:.1f} minutes."
            )
            return script
        except ValueError as exc:
            last_runtime_error = exc
            if attempt == 2:
                print(
                    "Full-script rewrites did not meet the runtime floor; "
                    "switching to focused narration-only chapter expansion."
                )
                script = _expand_longform_narration(
                    script, topic, category, language, target_words, minimum_words,
                    max_passes=2,
                )
                script = validate_longform_runtime(script, duration_max)
                print(
                    f"Expanded narration runtime gate passed: {script['narration_word_count']} words, "
                    f"approximately {script['estimated_narration_minutes']:.1f} minutes."
                )
                return script
            actual_words = count_longform_narration_words(script)
            missing_words = max(0, minimum_words - actual_words)
            repair_target = max(target_words, minimum_words + max(250, int(minimum_words * 0.15)))
            prompt += (
                "\n\nRUNTIME REPAIR REQUIRED: The previous complete documentary was structurally "
                "valid but too short. Its narration contained "
                f"{actual_words} spoken words; the hard minimum is {minimum_words}, "
                f"so it is short by {missing_words} words. Rewrite the COMPLETE JSON documentary, "
                f"targeting at least {repair_target} spoken words (not merely the minimum), "
                f"for a {duration_max}-minute runtime. Add meaningful, topic-specific explanation "
                "to the relevant chapters: verified mission chronology, engineering decisions, "
                "people involved, operational constraints, consequences and clearly stated evidence "
                "limitations where relevant. Preserve the existing factual caution and source traceability. "
                "Do not pad, repeat, invent facts, fabricate quotations, or add generic presenter filler. "
                "Keep the storyboard anchors exact and chronological, with scene-specific visual prompts. "
                "Return the full valid JSON object, not an outline."
            )
            print(
                f"Generated narration was short ({actual_words}/{minimum_words} words); "
                f"requesting evidence-led rewrite {attempt + 1}/2 with a {repair_target}-word target."
            )

    if last_generation_error:
        raise last_generation_error
    if last_runtime_error:
        raise last_runtime_error
    raise RuntimeError("Long-form runtime validation ended unexpectedly.")

def generate_seo(script: dict) -> dict:
    title = script.get("title", "")
    cat = script.get("category", "")
    tags = script.get("tags", ["finance", "hinglish", "education"])
    titles = [
        f"{title} | {CHANNEL_NAME}",
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
        "tags": tags + [cat, CHANNEL_NAME.lower(), "education"],
        "chapters": chapters,
        "pinned_comment": "कौन सा concept अगले video में समझाएँ? Comment में बताओ 👇",
        "hashtags": [f"#{t.replace(' ','')}" for t in tags[:5]],
    }


# Record script-generation outcome without storing prompt text.
from core.monitoring import monitored as _monitored
_ask_gemini = _monitored("gemini", "generate_script", _ask_gemini)
