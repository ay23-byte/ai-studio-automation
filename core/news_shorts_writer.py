"""Generate a short Hinglish news script only from a human-reviewed evidence packet."""
import json, os, re, requests
from core.news_source_verification import verify_packet

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
    if not any(term in combined for term in ('पक्की भविष्यवाणी नहीं', 'forecast नहीं', 'not a flood forecast')):
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


def generate_reviewed_shorts(review_path, output_path):
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
    model=os.getenv('GEMINI_MODEL','gemini-3.8-flash')
    prompt='''Write a 35–58 second Hindi/Hinglish YouTube news Short based ONLY on this human-reviewed packet.
Use natural spoken Devanagari Hindi in say; Roman Hinglish captions in caption. 4–6 scenes, 10–22 words per scene, about 75–125 spoken words total. Start with a precise hook, explain what happened, why it matters, and what is still unknown. Attribute reports. Do not add facts, dates, numbers, quotes or causal claims absent from packet. No alarmism or invented eyewitness reporting. End with a concise context/disclaimer and CTA.

CRITICAL SCIENTIFIC GUARDRAILS FOR THIS PACKET:
- In spoken Hindi, define HAND as “निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई” (vertical height above the nearest drainage). Repeat the definition in the caption on the first HAND scene. Never use “vertical river height” / “वर्टिकल रिवर ऊंचाई”. Never describe 0–50 m HAND as a 50-metre horizontal radius, distance, or buffer from a river.
- 293,800, 108,500 and 22,800 are modelled potential-exposure estimates, not counts of people already affected and not a prediction that they will be flooded.
- The 46% and 77% values refer to building footprint area, not the number of buildings.
- Do not claim that construction caused flooding or that construction itself increased flood probability. Describe the study as measuring potential exposure and building-footprint changes.
- The GLOF values refer to modelled glacial-lake outburst flood extents, not observed inundation.
- Include all key measures accurately: 293,800 people / 18%; Nepal 108,500 and 22,800; building footprint area +46%; building footprint area in modelled GLOF extents +77%.
- State clearly that modelled potential exposure is not a flood forecast. Use numeric figures in captions so editors can verify them quickly.
Return JSON only: {"title":"...","hook":"...","category":"news","format":"shorts","estimated_seconds":45,"sources":[{"source_id":"S1"}],"claims":[{"claim_id":"C1","text":"...","source_ids":["S1"]}],"scenes":[{"say":"...","caption":"...","label":"...","image":"English illustrative visual prompt; no real people or logos","keywords":["..."],"source_ids":["S1"]}]}
Every material factual statement in each scene must map to source_ids. Mark visual prompts illustrative and not documentary evidence.
REVIEWED PACKET:
'''+json.dumps(packet,ensure_ascii=False)
    # Retry the configured model once through a second supported Flash model.
    # A second generation attempt also repairs validator feedback (for example,
    # a spoken HAND reference that omits the nearest-drainage definition).
    model_candidates = [model]
    if model != 'gemini-3.6-flash':
        model_candidates.append('gemini-3.6-flash')
    payload = {'contents': [{'parts': [{'text': prompt}]}],
               'generationConfig': {'responseMimeType': 'application/json',
                                    'temperature': 0.3}}
    script = None
    last_error = None
    for draft_attempt in range(2):
        response = None
        for candidate_model in model_candidates:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{candidate_model}:generateContent'
            try:
                candidate_response = requests.post(
                    url, params={'key': key}, json=payload, timeout=(15, 75))
                candidate_response.raise_for_status()
                response = candidate_response
                break
            except requests.RequestException as exc:
                last_error = exc
        if response is None:
            raise RuntimeError(
                'Gemini script generation failed on configured and fallback models. '
                'Check Google AI API availability and the GEMINI_MODEL setting.'
            ) from last_error
        data = response.json()
        try:
            candidate_text = ''.join(
                p.get('text', '') for p in data['candidates'][0]['content']['parts'])
            candidate_script = json.loads(candidate_text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            last_error = ValueError('Gemini returned no usable JSON script candidate.')
        else:
            candidate_scenes = candidate_script.get('scenes', [])
            candidate_words = sum(
                len(re.findall(r'\\S+', scene.get('say', '')))
                for scene in candidate_scenes)
            try:
                if not 4 <= len(candidate_scenes) <= 6:
                    raise ValueError('Generated script must contain 4–6 scenes.')
                if not 70 <= candidate_words <= 140:
                    raise ValueError(
                        f'Script word count outside safe target: {candidate_words}.')
                _validate_scientific_wording(candidate_script)
            except ValueError as exc:
                last_error = exc
            else:
                script = candidate_script
                words = candidate_words
                break
        if draft_attempt == 0:
            payload['contents'][0]['parts'][0]['text'] = (
                prompt
                + '\\n\\nCORRECTION REQUIRED: Your previous JSON draft failed validation: '
                + str(last_error)
                + '\\nReturn a corrected complete JSON script. Every scene whose spoken '
                  'say field contains the term HAND must, in that same say field, explicitly '
                  'say that HAND means “निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई”. '
                  'Do not rely on a caption or another scene for this definition. '
                  'Prefer spelling out the definition rather than saying HAND alone. '
                  'Preserve all verified figures and scientific caveats.')
    if script is None:
        raise ValueError(
            'Generated script failed validation after one correction attempt: '
            + str(last_error)
        )
    scenes = script.get('scenes', [])\n    known={s['source_id'] for s in sources}
    for claim in script.get('claims',[]):
        if not set(claim.get('source_ids',[])).issubset(known): raise ValueError('Generated claim has unknown source IDs.')
    for scene in scenes:
        if not set(scene.get('source_ids',[])).issubset(known): raise ValueError('Scene has unknown source IDs.')
    script['source_verification']=verification
    script['editorial_status']='draft_requires_human_script_and_visual_review'
    script['word_count']=words
    script['reviewed_packet_path']=str(review_path)
    with open(output_path,'w',encoding='utf-8') as f: json.dump(script,f,ensure_ascii=False,indent=2)
    return script
