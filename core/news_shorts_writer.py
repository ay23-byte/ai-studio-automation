"""Generate a short Hinglish news script only from a human-reviewed evidence packet."""
import json, os, re, time, requests
from core import gemini as gemini_policy
from core.news_source_verification import verify_packet

def _normalize_spoken_hand_definitions(script):
    """Deterministically add the required same-scene HAND definition before validation.

    This is a wording normalization, not a scientific bypass: the exact definition
    is inserted into narration wherever HAND is spoken, then every strict validator
    and the final word-count limit still run on the normalized script.
    """
    definition = 'HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई'
    drainage_terms = ('nearest drainage', 'निकटतम जल-निकासी', 'निकटतम नाले', 'निकटतम जलधारा')
    for scene in script.get('scenes', []):
        say = str(scene.get('say', ''))
        if 'hand' in say.lower() and not any(term in say.lower() for term in drainage_terms):
            scene['say'] = definition + '। ' + say
    return script


def _normalize_spoken_building_footprint_labels(script):
    """Make each spoken 46%/77% claim name building-footprint area before validation.

    This only adds the metric's already-verified unit to the same spoken scene.
    The strict scientific validator still checks the figures, context, caveats,
    and scene-level wording after normalization.
    """
    area_terms = ('भवन क्षेत्रफल', 'भवन-फुटप्रिंट क्षेत्रफल',
                  'building footprint area', 'building area')
    for scene in script.get('scenes', []):
        say = str(scene.get('say', ''))
        lower_say = say.lower()
        if any(figure in say for figure in ('46%', '77%')) and not any(
                term in lower_say for term in area_terms):
            scene['say'] = 'भवन-फुटप्रिंट क्षेत्रफल में ' + say
    return script


def _validate_scientific_wording(script):
    """Reject common overclaims and unit/metric confusions before artifact creation."""
    scenes = script.get('scenes', [])
    combined = ' '.join(
        str(scene.get(key, ''))
        for scene in scenes
        for key in ('say', 'caption')
    ).lower()
    forbidden = (
        'अनियंत्रित निर्माण',
        'निर्माण से बाढ़ का खतरा बढ़',
        '50 मीटर दायरे',
        '50m radius',
        'people are affected',
        'लोग प्रभावित हो सकते हैं',
        'vertical river height',
        'वर्टिकल रिवर ऊंचाई',
        'वर्टिकल रिवर हाइट',
        'नदी से 50 मीटर',
        '50 मीटर की दूरी',
    )
    found = [phrase for phrase in forbidden if phrase in combined]
    if found:
        raise ValueError('Script contains unsupported causal, exposure, or distance wording: ' + ', '.join(found))
    if not any(term in combined for term in ('hand', 'nearest drainage', 'ऊँचाई')):
        raise ValueError('Script must explain HAND as a vertical height-above-drainage measure.')
    drainage_terms = ('nearest drainage', 'निकटतम जल-निकासी', 'निकटतम नाले', 'निकटतम जलधारा')
    if not any(term in combined for term in drainage_terms):
        raise ValueError('Script must define HAND as vertical height above the nearest drainage.')
    # The definition must appear in spoken narration/captions, not only in a generated visual prompt.
    spoken_and_captioned = ' '.join(str(scene.get(key, '')) for scene in scenes for key in ('say', 'caption')).lower()
    if not any(term in spoken_and_captioned for term in drainage_terms):
        raise ValueError('HAND nearest-drainage definition must appear in narration or captions.')
    if any('hand' in str(scene.get('say', '')).lower() and not any(term in str(scene.get('say', '')).lower() for term in drainage_terms) for scene in scenes):
        raise ValueError('Any spoken HAND reference must explain nearest drainage in the same scene.')
    if not any(term in combined for term in ('संभावित', 'potential exposure', 'modelled exposure')):
        raise ValueError('Script must describe the figures as potential/modelled exposure.')
    if not any(term in combined for term in ('पक्की भविष्यवाणी नहीं', 'बाढ़ का पूर्वानुमान नहीं', 'forecast नहीं', 'not a flood forecast')):
        raise ValueError('Script must explicitly say this is not a flood forecast.')
    if not any(term in combined for term in ('भवन क्षेत्र', 'भवन-फुटप्रिंट', 'building footprint area', 'building area')):
        raise ValueError('Script must identify the 46%/77% values as building area/footprint area.')
    required_groups = (
        ('293,800', '2,93,800', '293800'),
        ('108,500', '1,08,500', '108500'),
        ('22,800',),
        ('46%',),
        ('77%',),
    )
    for group in required_groups:
        if not any(value in combined for value in group):
            raise ValueError('Script omitted a required study figure: ' + ' / '.join(group))
    spoken = ' '.join(str(scene.get('say', '')).lower() for scene in scenes)
    if not re.search(r'(108,500|1,08,500|108500).{0,100}(0–50|0-50).{0,30}hand', spoken):
        raise ValueError('Spoken narration must identify 108,500 as the Nepal 0–50 m HAND estimate.')
    if not re.search(r'22,800.{0,100}(0–10|0-10).{0,30}hand', spoken):
        raise ValueError('Spoken narration must identify 22,800 as the Nepal 0–10 m HAND estimate.')
    for figure in ('46%', '77%'):
        matching = [str(scene.get('say', '')).lower() for scene in scenes if figure in str(scene.get('say', ''))]
        if not matching or not any(('भवन क्षेत्रफल' in s or 'भवन-फुटप्रिंट क्षेत्रफल' in s or 'building footprint area' in s or 'building area' in s) for s in matching):
            raise ValueError(f'{figure} must be described in spoken narration as building footprint area.')
    overconfident = ('निर्माण ने बाढ़ की संभावना नहीं बढ़ाई', 'construction did not increase flood probability', 'construction has not increased flood probability')
    if any(term in combined for term in overconfident):
        raise ValueError('Caveat overstates the evidence; do not claim construction had no effect.')
    causal_uncertainty = (
        'अध्ययन यह साबित नहीं करता कि निर्माण',
        'अध्ययन यह स्थापित नहीं करता कि निर्माण',
        'कारण-परिणाम साबित नहीं करता',
        'does not establish that construction',
        'does not prove that construction',
    )
    no_probability_conclusion = (
        'यह भी नहीं बताता कि निर्माण से बाढ़ की संभावना बढ़ी या नहीं',
        'यह नहीं बताता कि निर्माण से बाढ़ की संभावना बढ़ी या नहीं',
        'does not establish whether construction increased flood probability',
        'does not determine whether construction increased flood probability',
    )
    if not any(term in combined for term in causal_uncertainty) or not any(
        term in combined for term in no_probability_conclusion
    ):
        raise ValueError('Script must distinguish lack of causal evidence from proof of no effect.')



def _gemini_retry_delay(response, attempt):
    """Bounded wait before the next attempt on the same model.

    Honours Retry-After (seconds or HTTP-date, clamped to 60 s), otherwise waits
    10/30/60 s.  Up to one second of jitter is added so that scheduled runs which
    hit the same outage do not all retry at the same instant.
    """
    headers = getattr(response, 'headers', None) if response is not None else None
    raw = headers.get('Retry-After') if hasattr(headers, 'get') else None
    return gemini_policy.backoff_delay(
        attempt, retry_after=raw, schedule=(10, 30, 60), cap=60, jitter=1.0)


def _gemini_error_detail(response):
    """Return a bounded diagnostic without exposing request credentials."""
    if response is None:
        return ''
    try:
        detail = re.sub(r'\s+', ' ', str(response.text)).strip()
    except Exception:
        detail = ''
    return f' Response: {detail[:400]}' if detail else ''


def _post_gemini_with_retries(url, key, payload, *, attempts=3):
    """Retry transient Gemini outages with backoff and preserve useful API diagnostics.

    This reduces failures caused by short-lived 429/5xx incidents. It cannot make
    an upstream provider outage disappear; callers still receive a clear error
    after the bounded retry budget is exhausted.
    """
    retryable_statuses = {408, 429, 500, 502, 503, 504}
    last_error = None
    last_response = None
    for attempt in range(attempts):
        try:
            # Header auth: a key in the query string leaks into exception text and logs.
            response = requests.post(
                url, headers={'x-goog-api-key': key}, json=payload, timeout=(10, 45))
            last_response = response
            if gemini_policy.classify(response.status_code, getattr(response, 'text', '')) == gemini_policy.AUTH:
                # A rejected key fails the same way on every attempt and every
                # model.  Stop at once with the action the operator must take.
                raise gemini_policy.GeminiCredentialError(
                    gemini_policy.describe(gemini_policy.AUTH) + f' (HTTP {response.status_code})')
            if response.status_code in retryable_statuses:
                detail = _gemini_error_detail(response)
                last_error = requests.HTTPError(
                    f'Gemini temporarily unavailable (HTTP {response.status_code}).{detail}',
                    response=response)
                if attempt + 1 < attempts:
                    time.sleep(_gemini_retry_delay(response, attempt))
                    continue
                break
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            last_response = getattr(exc, 'response', None)
            status = getattr(last_response, 'status_code', None)
            if status is not None and status not in retryable_statuses:
                raise
            if attempt + 1 < attempts:
                time.sleep(_gemini_retry_delay(last_response, attempt))
    raise RuntimeError(
        f'Gemini request failed after {attempts} attempts: {last_error}'
    ) from last_error


 
def _gemini_model_candidates(configured_model, fallback_models=None):
    """Return configured Gemini candidates in priority order, without duplicates.

    GEMINI_FALLBACK_MODELS is a comma-separated list so GitHub Actions and local
    runs can select currently available models without a code change.  When it is
    not set, the shared default chain in ``core.gemini`` is used (this module no
    longer keeps its own, shorter list).  Invalid ids raise a clear config error.
    """
    return gemini_policy.model_chain(configured_model, fallback_models)


def _build_correction_prompt(prompt, error):
    """Build a targeted second-pass instruction without relaxing validation."""
    message = str(error)
    correction = (
        "\n\nCORRECTION REQUIRED: Your previous JSON draft failed validation: "
        + message
        + "\nReturn a corrected complete JSON script. Preserve all verified facts, "
          "figures, source IDs, scientific caveats, and 4–6 scenes. Do not weaken "
          "scientific guardrails. Treat the HAND definition as a scene-level requirement, "
          "not a script-level requirement: whenever a scene's say field contains the word "
          "HAND, that same say field MUST contain the complete phrase "
          "“HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई”. Do not put the definition "
          "only in a caption or another scene. Prefer mentioning HAND only in the definition "
          "scene and the Nepal metrics scene. In the Nepal metrics scene, use a compact "
          "spoken construction such as: “HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर "
          "ऊँचाई; नेपाल में 108,500 लोग 0–50 मीटर और 22,800 लोग 0–10 मीटर HAND सीमा "
          "में हैं.” Keep each say field concise while preserving all verified facts. "
          "Do not use radius, horizontal-distance, or river-buffer wording. Keep all "
          "required Nepal ranges and identify both percentages as building footprint area."
    )
    match = re.search(r'Script word count outside safe target: (\d+)', message)
    if match:
        count = int(match.group(1))
        if count > 140:
            correction += (
                f"\nWORD-COUNT REPAIR: The previous narration has {count} spoken words; "
                "the hard maximum is 140. Rewrite more concisely and target 85–100 "
                "spoken words. Keep each scene's say field to at most 22 whitespace-separated "
                "words. Remove repetition and shorten transitions, not verified figures, "
                "HAND definitions, Nepal ranges, percentage meanings, or caveats. Count all "
                "say fields before returning JSON; the total MUST be 70–140."
            )
        elif count < 70:
            correction += (
                f"\nWORD-COUNT REPAIR: The previous narration has {count} spoken words; "
                "the minimum is 70. Expand only with concise context supported by the "
                "reviewed packet; target 85–100 words. Do not invent facts."
            )
    return prompt + correction


def _coerce_script_payload(payload):
    """Normalize Gemini JSON variants to the writer's canonical object shape."""
    if isinstance(payload, list):
        # Some Gemini responses contain the scene array directly despite the
        # prompt requesting an object. Preserve the scenes and let all normal
        # structural/scientific validation run below.
        payload = {"scenes": payload}
    if not isinstance(payload, dict):
        raise ValueError("Gemini script JSON must be an object or a scene array.")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Gemini script JSON must contain a scenes array.")
    return payload


def generate_reviewed_shorts(review_path, output_path, language=None):
    packet=json.loads(open(review_path,encoding='utf-8').read())
    if packet.get('review_status')!='approved':
        raise ValueError("Story packet must have review_status='approved' after human source review.")
    facts=packet.get('verified_facts')
    if not isinstance(facts,list) or not facts:
        raise ValueError('Approved packet must contain verified_facts.')
    for i,f in enumerate(facts,1):
        if not f.get('fact') or not f.get('source_ids') or not f.get('evidence_note'):
            raise ValueError(f'Fact {i} needs fact, source_ids and evidence_note.')
    sources=packet.get('sources')
    if not isinstance(sources,list) or not sources:
        raise ValueError('Approved packet must include inspected original sources.')
    source_ids={s.get('source_id') for s in sources}
    for f in facts:
        if not set(f['source_ids']).issubset(source_ids): raise ValueError('Fact references an unknown source_id.')
    verification = verify_packet(packet, breaking=packet.get('story_type', 'breaking') != 'explainer')
    if not verification['safe_to_draft']:
        reasons = '; '.join(f"fact {r['fact_index']}: {r['reason']}" for r in verification['fact_checks'] if not r['ok'])
        raise ValueError('Source verification gate rejected this packet: ' + reasons)
    key=os.getenv('GEMINI_API_KEY','')
    if not key: raise RuntimeError('GEMINI_API_KEY is required to draft a script.')
    story_identity = " ".join(str(packet.get(k, "")) for k in ("story_title", "title", "story_id")).lower()
    is_himalayan_packet = "himalay" in story_identity
    default_language = "hindi" if is_himalayan_packet else "hinglish"
    language = str(language or packet.get('language') or default_language).strip().lower()
    if language not in {'english', 'hinglish', 'hindi'}:
        raise ValueError("language must be one of: english, hinglish, hindi")
    if is_himalayan_packet and language != "hindi":
        raise ValueError(
            "This Himalayan study currently has Hindi-only scientific wording validators. "
            "Choose Hindi for this packet; English/Hinglish support is enabled for other reviewed stories."
        )
    language_instruction = {
        "english": "Write natural spoken English narration and accurate English captions.",
        "hinglish": "Write natural spoken Roman Hinglish narration and accurate Roman Hinglish captions. Keep English technical terms natural.",
        "hindi": "Write natural spoken Devanagari Hindi narration and Roman Hinglish captions."
    }[language]
    prompt = (
        "Write a 35–58 second YouTube news Short based ONLY on this human-reviewed packet. "
        + language_instruction
        + " Use 4–6 scenes, ideally 10–16 words per scene, target 85–100 spoken words total "
        "(hard validation range 70–140 words). Start with a precise hook, explain what happened, "
        "why it matters, and what is still unknown. Attribute reports. Do not add facts, dates, "
        "numbers, quotes or causal claims absent from the packet. No alarmism or invented eyewitness "
        "reporting. End with concise context/disclaimer and CTA. Return valid JSON with title, hook, "
        "category, format, estimated_seconds, sources, claims, and 4–6 scenes. Each scene must include "
        "say, caption, label, an English illustrative image prompt, keywords, and source_ids. "
        "Every material factual statement must map to source_ids; visuals are illustrative, not evidence.\n"
    )
    if is_himalayan_packet:
        prompt += """\nCRITICAL SCIENTIFIC GUARDRAILS FOR THIS PACKET:
- HAND is a strict same-scene spoken requirement: if a scene's say field contains “HAND”, that same say field MUST contain the full phrase “HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई”. A definition in another scene or in a caption does NOT satisfy this rule. Do not say HAND in any other narration scene unless the full definition is repeated there. In the Nepal metrics scene, put the definition and both Nepal estimates together in one concise spoken sentence: “HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई; नेपाल में 108,500 लोग 0–50 मीटर और 22,800 लोग 0–10 मीटर HAND सीमा में हैं.” Repeat the definition in the caption on the first HAND scene. Never use “vertical river height” / “वर्टिकल रिवर ऊंचाई”. Never call the 0–50 m HAND band a “50 मीटर दायरे”, “50m radius”, “50 metre zone”, distance, or buffer from a river. The phrase “0–50 मीटर HAND सीमा” may be used only after the vertical-height definition is stated in that same scene. HAND is a vertical elevation measure, not a horizontal radius around a river.
- 293,800, 108,500 and 22,800 are modelled potential-exposure estimates, not counts of people already affected and not a prediction that they will be flooded.
- The 46% and 77% values refer to building footprint area, not the number of buildings.
- Do not claim that construction caused flooding. Do not claim that construction did NOT increase flood probability: absence of causal evidence is not proof of no effect. Say the study does not establish whether construction caused flooding or increased its probability.
- In spoken narration, attach Nepal's 108,500 figure to 0–50 m HAND and 22,800 to 0–10 m HAND; do not leave these bands only in captions.
- In spoken narration, explicitly identify BOTH 46% and 77% as building footprint area; the 77% measure is within modelled GLOF extents.
- The GLOF values refer to modelled glacial-lake outburst flood extents, not observed inundation.
- Include all key measures accurately: 293,800 people / 18%; Nepal 108,500 and 22,800; building footprint area +46%; building footprint area in modelled GLOF extents +77%.
- State clearly that modelled potential exposure is not a flood forecast. Use numeric figures in captions so editors can verify them quickly.
"""
    prompt += "\nREVIEWED PACKET:\n" + json.dumps(packet, ensure_ascii=False)
    model = os.getenv('GEMINI_MODEL', '').strip() or gemini_policy.DEFAULT_MODEL
    # Retry the configured model once through a second supported Flash model.
    # A second generation attempt also repairs validator feedback (for example,
    # a spoken HAND reference that omits the nearest-drainage definition).
    model_candidates = _gemini_model_candidates(model)
    clock = gemini_policy.Stopwatch()
    attempted_models, model_failures, model_used = [], [], None
    payload = {'contents': [{'parts': [{'text': prompt}]}],
               'generationConfig': {'responseMimeType': 'application/json',
                                    'temperature': 0.3}}
    script = None
    last_error = None
    for draft_attempt in range(3):
        response = None
        for candidate_model in list(model_candidates):
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{candidate_model}:generateContent'
            if candidate_model not in attempted_models:
                attempted_models.append(candidate_model)
            try:
                response = _post_gemini_with_retries(url, key, payload)
                model_used = candidate_model
                # Later correction drafts start with the model that just answered
                # instead of walking through the unavailable ones again.
                model_candidates = [candidate_model] + [m for m in model_candidates if m != candidate_model]
                break
            except gemini_policy.GeminiCredentialError as exc:
                # Same key, same answer on every model: do not try the others.
                gemini_policy.record_usage(
                    'news_script', model=None, outcome=gemini_policy.AUTH,
                    attempted=attempted_models, failures=[f'{candidate_model}: {exc}'],
                    seconds=clock.seconds())
                raise
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                model_failures.append(f'{candidate_model}: {exc}')
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status == 404 and len(model_candidates) > 1:
                    # A retired/unknown id will not start working mid-run.
                    model_candidates = [m for m in model_candidates if m != candidate_model]
        if response is None:
            attempted = ', '.join(attempted_models)
            gemini_policy.record_usage(
                'news_script', model=None, outcome='unavailable', attempted=attempted_models,
                failures=model_failures, seconds=clock.seconds())
            raise RuntimeError(
                'Gemini script generation failed after retries on all configured '
                f'model candidates ({attempted}). Last error: {last_error}. '
                'Check Google AI API availability, quota/billing, credentials, '
                'network status, and GEMINI_MODEL.'
            ) from last_error
        try:
            # A 200 with a non-JSON body (proxy/HTML error page) is a malformed
            # response, handled like any other unusable draft, not a crash.
            data = response.json()
            candidate_text = gemini_policy.response_text(data)
            if not candidate_text.strip():
                raise KeyError('candidates')
            candidate_script = _coerce_script_payload(json.loads(candidate_text))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = ValueError('Gemini returned no usable JSON script candidate.')
        else:
            candidate_scenes = candidate_script.get('scenes', [])
            if is_himalayan_packet:
                _normalize_spoken_hand_definitions(candidate_script)
                _normalize_spoken_building_footprint_labels(candidate_script)
            candidate_scenes = candidate_script.get('scenes', [])
            candidate_words = sum(
                len(re.findall(r'\S+', scene.get('say', '')))
                for scene in candidate_scenes)
            try:
                if not 4 <= len(candidate_scenes) <= 6:
                    raise ValueError('Generated script must contain 4–6 scenes.')
                if not 70 <= candidate_words <= 140:
                    raise ValueError(
                        f'Script word count outside safe target: {candidate_words}.')
                if is_himalayan_packet:
                    _validate_scientific_wording(candidate_script)
            except ValueError as exc:
                last_error = exc
            else:
                script = candidate_script
                words = candidate_words
                break
        if draft_attempt < 2:
            correction_prompt = _build_correction_prompt(prompt, last_error) if is_himalayan_packet else prompt + '\nCORRECTION REQUIRED: ' + str(last_error) + '. Return corrected JSON in the selected language; preserve verified facts and source IDs.'
            if draft_attempt == 1:
                correction_prompt += (
                    "\n\nFINAL COMPRESSION PASS: The prior correction still failed. "
                    "Keep every scene's say field to at most 22 whitespace-separated words. "
                    "Use six concise scenes if needed. Preserve every required number, "
                    "its meaning and range, the HAND definition, and both scientific caveats. "
                    "Return only complete JSON and ensure total say-field word count is 70–140."
                )
            payload['contents'][0]['parts'][0]['text'] = correction_prompt
    if script is None:
        gemini_policy.record_usage(
            'news_script', model=model_used, outcome='validation_failed', attempted=attempted_models,
            failures=model_failures + [str(last_error)], seconds=clock.seconds())
        raise ValueError(
            'Generated script failed validation after three generation attempts (initial draft plus two targeted corrections): '
            + str(last_error)
        )
    scenes = script.get('scenes', [])
    known={s['source_id'] for s in sources}
    for claim in script.get('claims',[]):
        if not set(claim.get('source_ids',[])).issubset(known): raise ValueError('Generated claim has unknown source IDs.')
    for scene in scenes:
        if not set(scene.get('source_ids',[])).issubset(known): raise ValueError('Scene has unknown source IDs.')
    script['language'] = language
    script['source_verification']=verification
    script['editorial_status']='draft_requires_human_script_and_visual_review'
    script['word_count']=words
    script['reviewed_packet_path']=str(review_path)
    # Provenance: which model wrote this draft (the primary may have been skipped).
    script['generation'] = {
        'provider': 'gemini', 'model': model_used,
        'configured_model': model, 'attempted_models': attempted_models,
        'fallback_used': model_used != model,
    }
    gemini_policy.record_usage(
        'news_script', model=model_used, outcome='ok', attempted=attempted_models,
        failures=model_failures, seconds=clock.seconds())
    with open(output_path,'w',encoding='utf-8') as f: json.dump(script,f,ensure_ascii=False,indent=2)
    return script
