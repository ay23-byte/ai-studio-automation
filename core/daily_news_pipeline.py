"""Daily news Shorts research queue. Discovery is not verification."""
from __future__ import annotations
import hashlib, json, os, re, math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
import xml.etree.ElementTree as ET
from core.news_editorial import build_review_packets, similarity

# Broad discovery is intentionally separate from the source-verification gate.
# Prefer direct publisher feeds so candidate metadata retains publisher provenance.
FEEDS = [
 'https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en',
 'https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en',
 'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en',
 'https://feeds.bbci.co.uk/news/world/asia/india/rss.xml',
 'https://feeds.bbci.co.uk/news/technology/rss.xml',
 'https://feeds.bbci.co.uk/news/business/rss.xml',
 'https://feeds.bbci.co.uk/news/world/rss.xml',
 'https://www.thehindu.com/news/national/feeder/default.rss',
 'https://www.thehindu.com/business/feeder/default.rss',
 'https://indianexpress.com/feed/',
 'https://indianexpress.com/section/business/feed/',
 'https://indianexpress.com/section/technology/feed/',
 'https://www.theguardian.com/world/rss',
 'https://feeds.npr.org/1001/rss.xml',
]
USER_AGENT = 'AIStudioDailyNews/1.0 (+RSS metadata discovery)'
YOUTUBE_TRENDING_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_NEWS_CATEGORY_ID = "25"  # News & Politics


def fetch_youtube_news_trending(api_key=None, region_code="IN", max_results=50):
    """Find highly viewed recent news videos; a trend proxy, not YouTube's old Trending chart.

    YouTube retired the old Trending page and changed mostPopular in July 2025.
    We therefore use search.list ordered by viewCount, restricted to recent
    News & Politics videos in a region, then retrieve their current view counts.
    These videos are discovery signals only, never factual sources.
    """
    from datetime import timedelta

    key = str(api_key or os.getenv("YOUTUBE_DATA_API_KEY") or os.getenv("YOUTUBE_API_KEY")
              or os.getenv("YT_API_KEY") or "").strip()
    if not key:
        return {"available": False, "reason": "YOUTUBE_DATA_API_KEY is not configured", "videos": []}
    query = "India news" if region_code == "IN" else "world news"
    published_after = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds").replace("+00:00", "Z")
    response = requests.get("https://www.googleapis.com/youtube/v3/search", params={
        "part": "snippet", "type": "video", "q": query, "order": "viewCount",
        "publishedAfter": published_after, "regionCode": region_code,
        "videoCategoryId": YOUTUBE_NEWS_CATEGORY_ID, "maxResults": min(max(1, int(max_results)), 50),
        "key": key,
    }, timeout=20, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    search_items = payload.get("items", [])
    ids = [str(item.get("id", {}).get("videoId") or "") for item in search_items]
    ids = [video_id for video_id in ids if video_id]
    if not ids:
        return {"available": True, "region_code": region_code, "video_count": 0, "videos": [],
                "method": "YouTube Search API: recent News & Politics videos ordered by viewCount"}
    stats_response = requests.get(YOUTUBE_TRENDING_URL, params={
        "part": "snippet,statistics", "id": ",".join(ids[:50]), "maxResults": min(len(ids), 50),
        "key": key,
    }, timeout=20, headers={"User-Agent": USER_AGENT})
    stats_response.raise_for_status()
    details = {str(item.get("id")): item for item in stats_response.json().get("items", [])}
    videos = []
    for item in search_items:
        video_id = str(item.get("id", {}).get("videoId") or "")
        detail = details.get(video_id, {})
        snippet = detail.get("snippet") or item.get("snippet") or {}
        stats = detail.get("statistics") or {}
        title = str(snippet.get("title") or "").strip()
        if not title:
            continue
        videos.append({
            "video_id": video_id, "title": title[:300],
            "channel": str(snippet.get("channelTitle") or "")[:160],
            "published_at": str(snippet.get("publishedAt") or ""),
            "view_count": int(stats.get("viewCount") or 0),
            "url": "https://www.youtube.com/watch?v=" + video_id,
            "region_code": region_code, "category_id": YOUTUBE_NEWS_CATEGORY_ID,
        })
    videos.sort(key=lambda item: item["view_count"], reverse=True)
    return {"available": True, "region_code": region_code, "video_count": len(videos), "videos": videos,
            "method": "YouTube Search API: recent News & Politics videos ordered by viewCount",
            "published_within_hours": 48}


def apply_youtube_trend_signals(candidates, trending_videos):
    """Attach title-overlap discovery signals without treating videos as sources."""
    for candidate in candidates:
        title = str(candidate.get("title") or "")
        matches = []
        for video in trending_videos:
            score = similarity(title, video.get("title", ""))
            if score >= 0.16:
                matches.append({
                    "title": video["title"], "channel": video.get("channel", ""),
                    "url": video.get("url", ""), "view_count": video.get("view_count", 0),
                    "similarity": round(score, 3),
                    "region_code": video.get("region_code", ""),
                    "popularity_score": round(score * (0.5 + 0.5 * min(1.0, math.log10(max(1, int(video.get("view_count", 0)) + 1)) / 7)), 3),
                })
        matches.sort(key=lambda item: (item["popularity_score"], item["view_count"]), reverse=True)
        candidate["youtube_trend_score"] = round(max((m["popularity_score"] for m in matches), default=0.0), 3)
        candidate["youtube_trend_matches"] = matches[:3]
    return candidates


def _text(node, path, default=''):
    child = node.find(path)
    return (child.text or '').strip() if child is not None else default

def _plain(html):
    """Extract RSS text while omitting Google News' styled publisher-credit labels."""
    import re as _re
    from html.parser import HTMLParser

    class _DescriptionText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag.lower() == 'font':
                self.skip_depth += 1

        def handle_endtag(self, tag):
            if tag.lower() == 'font' and self.skip_depth:
                self.skip_depth -= 1

        def handle_data(self, data):
            if not self.skip_depth:
                self.parts.append(data)

    parser = _DescriptionText()
    parser.feed(str(html or ''))
    parser.close()
    return _re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()

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
        # Aggregator feeds link to a redirect; the real publisher is the
        # <source url="..."> attribute. Keep it so source tiers can be checked.
        source_node = node.find('source')
        publisher_url = (source_node.attrib.get('url', '') if source_node is not None else '').strip()
        rows.append({'id': _item_id(link), 'title': title[:300], 'url': link,
                     'publisher': (_text(node, 'source') or urlparse(url).netloc)[:120],
                     'publisher_url': publisher_url[:300],
                     'summary': _plain(_text(node, 'description'))[:600],
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
    found, errors, feed_health = [], [], []
    configured_feeds = list(FEEDS if feeds is None else feeds)
    for url in configured_feeds:
        try:
            rows = collect_feed(url)[:limit_per_feed]
            found.extend(rows)
            feed_health.append({'feed': url, 'status': 'ok', 'candidate_count': len(rows)})
        except Exception as exc:
            error = {'feed': url, 'error': str(exc)[:300]}
            errors.append(error)
            feed_health.append({'feed': url, 'status': 'error', 'candidate_count': 0,
                                'error': error['error']})
    unique, seen = [], set()
    for item in found:
        if item['id'] not in seen:
            seen.add(item['id']); unique.append(item)
    unique.sort(key=lambda x: x.get('published', ''), reverse=True)
    trend_report = {"available": False, "reason": "not requested", "videos": []}
    if (os.getenv("YOUTUBE_DATA_API_KEY") or os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY")):
        trend_reports = []
        trend_errors = []
        for region_code in ("IN", "US"):
            try:
                trend_reports.append(fetch_youtube_news_trending(region_code=region_code))
            except Exception as exc:
                trend_errors.append({"region_code": region_code, "error": str(exc)[:250]})
        trend_videos = [video for report in trend_reports for video in report.get("videos", [])]
        apply_youtube_trend_signals(unique, trend_videos)
        trend_report = {
            "available": bool(trend_videos), "regions_checked": ["IN", "US"],
            "region_video_counts": {report.get("region_code"): report.get("video_count", 0)
                                    for report in trend_reports},
            "video_count": len(trend_videos), "errors": trend_errors,
            "global_proxy": "US recent News & Politics search; not a worldwide aggregate",
        }
    else:
        for item in unique:
            item["youtube_trend_score"] = 0.0
            item["youtube_trend_matches"] = []
    return {'collected_at': datetime.now(timezone.utc).isoformat(),
            'feed_count': len(configured_feeds),
            'healthy_feed_count': sum(1 for item in feed_health if item['status'] == 'ok'),
            'failed_feed_count': len(errors), 'feed_health': feed_health,
            'candidate_count': len(unique), 'candidates': unique, 'feed_errors': errors,
            'youtube_trending': {k: v for k, v in trend_report.items() if k != 'videos'},
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
    data['ranked_story_clusters'] = build_review_packets(data['candidates'])
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
