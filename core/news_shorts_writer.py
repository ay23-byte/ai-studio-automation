"""Generate a short Hinglish news script only from a human-reviewed evidence packet."""
import json, os, re, requests
from core.news_source_verification import verify_packet

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
Return JSON only: {"title":"...","hook":"...","category":"news","format":"shorts","estimated_seconds":45,"sources":[{"source_id":"S1"}],"claims":[{"claim_id":"C1","text":"...","source_ids":["S1"]}],"scenes":[{"say":"...","caption":"...","label":"...","image":"English illustrative visual prompt; no real people or logos","keywords":["..."],"source_ids":["S1"]}]}
Every material factual statement in each scene must map to source_ids. Mark visual prompts illustrative and not documentary evidence.
REVIEWED PACKET:
'''+json.dumps(packet,ensure_ascii=False)
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    response=requests.post(url,params={'key':key},json={'contents':[{'parts':[{'text':prompt}]}],
      'generationConfig':{'responseMimeType':'application/json','temperature':0.3}},timeout=90)
    response.raise_for_status()
    data=response.json(); text=''.join(p.get('text','') for p in data['candidates'][0]['content']['parts'])
    script=json.loads(text)
    scenes=script.get('scenes',[])
    words=sum(len(re.findall(r'\S+',s.get('say',''))) for s in scenes)
    if not 4<=len(scenes)<=6: raise ValueError('Generated script must contain 4–6 scenes.')
    if not 70<=words<=140: raise ValueError(f'Script word count outside safe target: {words}.')
    known={s['source_id'] for s in sources}
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
