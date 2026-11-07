"""Editorial clustering and transparent heuristic ranking for news candidates."""
from __future__ import annotations
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

STOP = {'the','a','an','and','or','to','of','in','on','for','with','as','by','is','are','was','were','will','after','over','into','from','at','its','their','this','that','says','say','new','india'}
def tokens(title):
    return {w for w in re.findall(r'[a-z0-9]{3,}', (title or '').lower()) if w not in STOP}
def similarity(a,b):
    x,y=tokens(a),tokens(b)
    return len(x&y)/len(x|y) if x and y else 0.0
def cluster_candidates(items, threshold=0.28):
    clusters=[]
    for item in items:
        best=None; best_score=0.0
        for cluster in clusters:
            score=max((similarity(item.get('title',''), other.get('title','')) for other in cluster), default=0)
            if score>best_score: best,best_score=cluster,score
        if best is not None and best_score>=threshold: best.append(item)
        else: clusters.append([item])
    result=[]
    for index, group in enumerate(clusters,1):
        publishers={urlparse(x.get('url','')).netloc.lower().removeprefix('www.') for x in group if x.get('url')}
        result.append({'cluster_id':f'N{index:03d}','cluster_title':max(group,key=lambda x:len(tokens(x.get('title','')))).get('title',''),
                       'candidate_ids':[x['id'] for x in group], 'headline_count':len(group),
                       'publisher_domain_count':len(publishers), 'publishers':sorted(publishers)})
    return result
def rank_clusters(clusters, items):
    by_id={x['id']:x for x in items}; now=datetime.now(timezone.utc)
    ranked=[]
    for c in clusters:
        group=[by_id[i] for i in c['candidate_ids'] if i in by_id]
        # Score is a discovery priority heuristic, never a truth/confidence score.
        recency=0
        dates=[]
        for item in group:
            raw=item.get('published','')
            try:
                from email.utils import parsedate_to_datetime
                dt=parsedate_to_datetime(raw)
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                dates.append(dt)
            except Exception: pass
        if dates:
            age=max(0,(now-max(dates)).total_seconds()/3600)
            recency=max(0,30-min(age,30))/30
        diversity=min(c['publisher_domain_count'],4)/4
        coverage=min(c['headline_count'],5)/5
        score=round(100*(0.5*recency+0.3*diversity+0.2*coverage),1)
        ranked.append({**c,'discovery_priority_score':score,'score_explanation':
          'Heuristic only: 50% recency, 30% distinct publisher domains, 20% headline count. Not a fact-check or reliability rating.'})
    return sorted(ranked,key=lambda x:x['discovery_priority_score'],reverse=True)
def build_review_packets(items):
    clusters=rank_clusters(cluster_candidates(items),items)
    packets=[]
    for c in clusters:
        group=[x for x in items if x['id'] in c['candidate_ids']]
        packets.append({**c,'candidate_sources':[{'candidate_id':x['id'],'headline':x['title'],'publisher':x['publisher'],
            'url':x['url'],'published':x.get('published',''),'discovery_feed':x.get('discovery_feed','')} for x in group],
            'review_status':'pending_human_verification','verified_facts':[],
            'review_checklist':['Open each original source URL and confirm it reports this same event.',
             'Check publication date, event date, geography, units and context.',
             'Record at least two independent sources or one authoritative primary source.',
             'Write concise facts in your own words and attach source IDs/URLs.',
             'Record uncertainty, material responses and what is not known.',
             'Reject unsupported allegations, communal framing and sensational claims.']})
    return packets
