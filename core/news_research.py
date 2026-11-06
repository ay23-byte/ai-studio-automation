"""Free GDELT-based research references for documentary scripts.

GDELT discovers recent coverage; it is not a fact-checker and does not grant
rights to reuse article text, photos, or broadcast footage.
"""
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def research_topic(topic, max_records=8, timeout=25):
    """Return a small, deduplicated list of recent article references.

    Failure is non-fatal: evergreen documentaries can still be produced
    without current-news references.
    """
    topic = " ".join(str(topic or "").split())[:180]
    if not topic:
        return []
    params = {
        "query": topic,
        "mode": "artlist",
        "format": "json",
        "sort": "datedesc",
        "maxrecords": max(1, min(int(max_records), 10)),
        "timespan": "7d",
    }
    try:
        response = requests.get(GDELT_URL, params=params, timeout=timeout,
                                headers={"User-Agent": "AI-Studio-Documentary/1.0"})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"News research unavailable (continuing without live references): {exc}")
        return []

    articles = []
    seen = set()
    for item in payload.get("articles", []) or []:
        url = str(item.get("url") or "").strip()
        title = " ".join(str(item.get("title") or "").split())
        if not url.startswith(("https://", "http://")) or not title or url in seen:
            continue
        seen.add(url)
        domain = str(item.get("domain") or urlparse(url).netloc).strip()
        date = str(item.get("seendate") or item.get("date") or "").strip()
        articles.append({
            "title": title[:240],
            "url": url,
            "publisher": domain[:120],
            "date": date[:40],
        })
        if len(articles) >= max_records:
            break
    return articles


def format_research_context(articles):
    """Create compact, clearly qualified source leads for the script prompt."""
    if not articles:
        return (
            "No recent-news references were retrieved. Do not invent citations "
            "or imply live reporting; flag time-sensitive claims for verification."
        )
    lines = [
        "RECENT NEWS LEADS (discovery references only; open and verify before "
        "treating any claim as established):"
    ]
    for index, item in enumerate(articles, 1):
        lines.append(
            f"{index}. {item['title']} | {item['publisher']} | "
            f"{item['date'] or 'date not supplied'} | {item['url']}"
        )
    lines.append(
        "Use these as leads, not proof. Do not quote article text. Distinguish "
        "confirmed facts, allegations, estimates and analysis. Put any claim "
        "that cannot be verified into fact_check_notes. Include relevant URLs "
        "in research_sources in the output JSON."
    )
    return "\n".join(lines)
