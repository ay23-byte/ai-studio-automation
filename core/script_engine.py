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
- Do not pad. Give each chapter enough space to explain its argument, evidence and implications.
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


def _ask_model(prompt: str, model: str, attempts: int = 4):
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
    from core.research_verification import append_source_citations_to_description
    script = append_source_citations_to_description(script)
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
