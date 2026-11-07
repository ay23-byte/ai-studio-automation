"""Daily news Shorts research queue. Discovery is not verification."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
import xml.etree.ElementTree as ET

FEEDS = [
 'https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en',
 'https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en',
 'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en',
 'https://feeds.bbci.co.uk/news/world/asia/india/rss.xml',
 'https://feeds.bbci.co.uk/news/technology/rss.xml',
]
USER_AGENT = 'AIStudioDailyNews/1.0 (+RSS metadata discovery)'

def _text(node, path, default=''):
    child = node.find(path)
    return (child.text or '').strip() if child is not None else default

def _item_id(url):
    return hashlib.sha256(url.strip().encode('utf-8')).hexdigest()[:20]

def collect_feed(url, timeout=20):
    '''Read RSS/Atom metadata only; never fetch linked article pages.'''
    response = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows = []
    for node in root.findall('.//item'):
        link, title = _text(node, 'link'), _text(node, 'title')
        if not link or not title: continue
        rows.append({'id': _item_id(link), 'title': title[:300], 'url': link,
                     'publisher': (_text(node, 'source') or urlparse(url).netloc)[:120],
                     'published': _text(node, 'pubDate'), 'discovery_feed': url})
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    for node in root.findall('.//a:entry', ns):
        title = _text(node, 'a:title')
        link_node = node.find('a:link', ns)
        link = link_node.attrib.get('href', '') if link_node is not None else ''
        if not link or not title: continue
        rows.append({'id': _item_id(link), 'title': title[:300], 'url': link,
                     'publisher': urlparse(url).netloc,
                     'published': _text(node, 'a:published') or _text(node, 'a:updated'),
                     'discovery_feed': url})
    return rows

def collect_candidates(feeds=None, limit_per_feed=35):
    found, errors = [], []
    for url in list(feeds or FEEDS):
        try: found.extend(collect_feed(url)[:limit_per_feed])
        except Exception as exc: errors.append({'feed': url, 'error': str(exc)[:300]})
    unique, seen = [], set()
    for item in found:
        if item['id'] not in seen:
            seen.add(item['id']); unique.append(item)
    unique.sort(key=lambda x: x.get('published', ''), reverse=True)
    return {'collected_at': datetime.now(timezone.utc).isoformat(),
            'candidate_count': len(unique), 'candidates': unique, 'feed_errors': errors,
            'notice': 'Discovery metadata only. No story has been independently verified.'}

def load_seen(path):
    try: return set(json.loads(Path(path).read_text(encoding='utf-8')).get('ids', []))
    except FileNotFoundError: return set()

def save_json(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def prepare_queue(output_dir='output/daily_news', state_dir='state/daily_news', feeds=None):
    from datetime import datetime, timezone
    out, state = Path(output_dir), Path(state_dir)
    data = collect_candidates(feeds)
    seen = load_seen(state / 'seen.json')
    data['candidates'] = [x for x in data['candidates'] if x['id'] not in seen]
    data['candidate_count'] = len(data['candidates'])
    data['editorial_status'] = 'awaiting_human_selection_and_source_verification'
    data['required_review'] = [
      'Open original source links; confirm event and publication dates.',
      'Require two independent reliable sources or one authoritative primary source.',
      'Separate confirmed facts, reported claims, opinion and unknowns.',
      'Reject unsafe, defamatory, communal, graphic or unverifiable claims.',
      'Check visual and music licences before rendering.'
    ]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    save_json(out / ('candidates_' + stamp + '.json'), data)
    save_json(out / 'latest_candidates.json', data)
    return out / 'latest_candidates.json'

def mark_seen(candidate_ids, state_dir='state/daily_news'):
    state = Path(state_dir); state.mkdir(parents=True, exist_ok=True)
    path = state / 'seen.json'; ids = load_seen(path)
    ids.update(str(x) for x in candidate_ids)
    save_json(path, {'ids': sorted(ids), 'updated_at': datetime.now(timezone.utc).isoformat()})
