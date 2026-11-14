"""Autonomous daily news Short: discover → corroborate → script → render → PRIVATE upload.

Root cause addressed: the scheduled news workflow only collected RSS headlines.
Every later step (selection, drafting, asset prep, render, upload) needed a
person to edit JSON and dispatch four more workflows, so no news Short was ever
produced automatically.

This path is deliberately conservative and fails closed:

* A story is eligible only if the existing source gate
  (``core.news_source_verification``) accepts it: one Tier-1 primary source or
  two independent eligible publishers including a Tier-2 outlet, all fresh.
* Sensitive subjects (deaths, crime, courts, elections, communal or military
  conflict, health scares, named allegations) are never auto-produced; they stay
  in the human review queue.
* The script may only restate what the corroborating headlines/summaries say,
  with spoken attribution. Every number in the script must appear in the source
  text, otherwise the script is rejected.
* The result is uploaded PRIVATE. Making it public is always a human decision.

If no story passes, nothing is produced and the run ends successfully with a
notification: "no publishable story" is a correct outcome, not a failure.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from core import config as cfg
from core import dedupe
from core.news_editorial import cluster_candidates, rank_clusters
from core.news_source_verification import classify_source, verify_fact_sources

KIND = "news_short"

# Subjects that need a human editor. Matched on whole words in headlines/summaries.
SENSITIVE_TERMS = (
    "dead", "death", "deaths", "dies", "died", "killed", "kills", "murder", "murdered", "suicide",
    "rape", "raped", "assault", "molest", "abuse", "kidnap", "kidnapped", "stabbed", "shot", "shooting",
    "blast", "bomb", "terror", "terrorist", "militant", "attack", "attacks", "war", "airstrike", "missile",
    "riot", "riots", "communal", "lynch", "lynching", "mob", "hindu", "muslim", "christian", "sikh",
    "caste", "dalit", "mosque", "temple", "church", "blasphemy",
    "arrest", "arrested", "accused", "alleged", "allegedly", "fir", "chargesheet", "court", "verdict",
    "convicted", "bail", "cbi", "scam", "fraud", "raid", "raids",
    "election", "elections", "poll", "polls", "vote", "votes", "bjp", "congress", "aap", "tmc", "minister",
    "outbreak", "virus", "pandemic", "epidemic", "cancer", "vaccine",
    "crash", "crashes", "derail", "derailed", "collapse", "collapsed", "fire", "flood", "floods",
    "earthquake", "cyclone", "landslide", "stampede", "accident", "injured", "casualties", "victim", "victims",
    "sex", "porn", "nude", "minor", "child",
)
_SENSITIVE = re.compile(r"(?<![a-z])(" + "|".join(map(re.escape, SENSITIVE_TERMS)) + r")(?![a-z])", re.I)

ATTRIBUTION_MARKERS = ("according to", "reports", "reported", "as per", "ke mutabik", "ke anusaar",
                       "ke anusar", "ki report", "के अनुसार", "के मुताबिक", "की रिपोर्ट")
SENSATIONAL = re.compile(r"\b(shocking|you won'?t believe|exposed|destroyed|slams|sansani|breaking)\b", re.I)


class NoPublishableStory(RuntimeError):
    """No candidate cleared the source and safety gates (a normal outcome)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _published_iso(raw: str) -> str:
    """RSS dates are RFC 822; the source gate expects ISO 8601."""
    if not raw:
        return ""
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _source_url(item: dict) -> str:
    """The publisher's own address (aggregator redirect links prove nothing)."""
    publisher_url = str(item.get("publisher_url") or "").strip()
    if publisher_url:
        return publisher_url
    url = str(item.get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    return "" if host.endswith("news.google.com") else url


def _clean_headline(title: str, publisher: str = "") -> str:
    """Aggregators append " - Publisher" to titles; remove only that exact suffix."""
    title = str(title or "").strip()
    suffix = " - " + str(publisher or "").strip()
    if len(suffix) > 3 and title.lower().endswith(suffix.lower()):
        title = title[: -len(suffix)].rstrip()
    return title


def is_sensitive(text: str, ignore=()) -> str | None:
    """First sensitive term in ``text``; publisher names ("The Hindu") are ignored."""
    text = str(text or "")
    for name in sorted({str(n) for n in ignore if str(n).strip()}, key=len, reverse=True):
        text = re.sub(re.escape(name), " ", text, flags=re.I)
    match = _SENSITIVE.search(text)
    return match.group(1).lower() if match else None


def evaluate_cluster(cluster: dict, by_id: dict, now: datetime | None = None) -> dict:
    """Run the deterministic source gate on one headline cluster."""
    items = [by_id[i] for i in cluster.get("candidate_ids", []) if i in by_id]
    sources = []
    for index, item in enumerate(items, 1):
        url = _source_url(item)
        sources.append({
            "source_id": f"S{index}", "url": url, "link": item.get("url", ""),
            "publisher": item.get("publisher", ""),
            "title": _clean_headline(item.get("title", ""), item.get("publisher", "")),
            "text_snippet": item.get("summary", ""),
            "published": _published_iso(item.get("published", "")),
            "candidate_id": item.get("id"),
            "tier": classify_source({"url": url}) if url else "unknown",
        })
    fact = {"fact": cluster.get("cluster_title", ""), "source_ids": [s["source_id"] for s in sources]}
    gate = verify_fact_sources(fact, sources, breaking=True, now=now or _now())
    combined = " ".join(f"{s['title']} {s['text_snippet']}" for s in sources)
    sensitive = is_sensitive(combined, ignore=[s["publisher"] for s in sources])
    accepted_ids = {s["source_id"] for s in gate.get("sources", [])}
    return {
        "cluster_id": cluster.get("cluster_id"),
        "headline": next((s["title"] for s in sources if s["source_id"] in accepted_ids),
                         sources[0]["title"] if sources else str(cluster.get("cluster_title", ""))),
        "sources": [s for s in sources if s["source_id"] in accepted_ids],
        "all_sources": sources, "gate": gate, "sensitive_term": sensitive,
        "eligible": bool(gate.get("ok")) and not sensitive,
        "reason": ("sensitive subject requires a human editor: '%s'" % sensitive) if sensitive
                  else gate.get("reason", ""),
        "discovery_priority_score": cluster.get("discovery_priority_score", 0),
    }


def select_story(candidates: list[dict], ledger: dedupe.Ledger | None = None,
                 now: datetime | None = None) -> tuple[dict | None, list[dict]]:
    """Return (best eligible story or None, audit trail of every cluster considered)."""
    ledger = ledger or dedupe.Ledger()
    by_id = {item["id"]: item for item in candidates if item.get("id")}
    ranked = rank_clusters(cluster_candidates(candidates), candidates)
    audit, chosen = [], None
    for cluster in ranked:
        result = evaluate_cluster(cluster, by_id, now)
        refs = [s["candidate_id"] for s in result["all_sources"] if s.get("candidate_id")]
        duplicate = dedupe.find_duplicate(KIND, result["headline"], source_refs=refs, ledger=ledger)
        if duplicate:
            result["eligible"] = False
            result["reason"] = f"already covered: {duplicate.get('match')}"
        audit.append({k: result[k] for k in ("cluster_id", "headline", "eligible", "reason")}
                     | {"publishers": sorted({s["publisher"] for s in result["all_sources"]})})
        if result["eligible"] and chosen is None:
            result["source_refs"] = refs
            chosen = result
    return chosen, audit


# --- script -----------------------------------------------------------------

NEWS_PROMPT = """Write ONE 35-55 second YouTube news Short for the channel "{channel}".

You may use ONLY the facts stated in the SOURCE HEADLINES AND SUMMARIES below. They are the
complete evidence. Do not add background, causes, numbers, names, quotes, dates, predictions
or opinions that are not written there. If the sources disagree, say so.

LANGUAGE: {language_rule}

RULES:
- 4-5 scenes, 10-22 spoken words per scene, 60-100 spoken words in total.
- Scene 1 states what happened and names a source in speech ("According to {first_publisher}, ...").
- At least one more scene attributes the information to a named publisher.
- Neutral, calm wording. No sensational words, no blame, no speculation, no advice.
- Final scene: say that this is a developing report based on the named sources, then a short follow call to action.
- "label": 1-3 short words, max 22 characters.
- "image": a GENERIC illustrative visual in English (objects, places, technology, abstract concepts).
  Never depict a real person, a logo, a flag, or the specific event itself.
- "keywords": 2-4 generic English stock-footage search terms.

SOURCE HEADLINES AND SUMMARIES:
{evidence}

Return ONLY valid JSON:
{{"title":"Neutral headline, max 80 chars","hook":"max 50 chars","category":"news","scenes":[{{"say":"...","caption":"...","label":"...","image":"...","keywords":["..."]}}]}}"""

_LANGUAGE_RULES = {
    "english": "Natural spoken English for narration and captions.",
    "hinglish": "Conversational Roman-script Hinglish (Latin letters) for narration and captions; keep proper nouns and technical terms in English.",
    "hindi": "Natural Devanagari Hindi narration; captions in the same language.",
}


def _evidence_text(story: dict) -> str:
    lines = []
    for source in story["sources"]:
        line = f"- [{source['publisher']}] {source['title']}"
        if source.get("text_snippet"):
            line += f" — {source['text_snippet'][:300]}"
        lines.append(line)
    return "\n".join(lines)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))}


def validate_news_script(script: dict, story: dict, language: str = "english") -> list[str]:
    """Deterministic grounding checks; returns a list of problems (empty = accepted)."""
    from core.content_qa import validate_shorts_script
    problems = list(validate_shorts_script(script, language)["issues"])
    scenes = [s for s in script.get("scenes", []) if isinstance(s, dict)]
    spoken = " ".join(str(s.get("say") or "") for s in scenes)
    everything = " ".join([spoken, str(script.get("title") or ""),
                           " ".join(str(s.get("caption") or "") for s in scenes)])
    lowered = spoken.lower()
    publishers = [str(s["publisher"]) for s in story["sources"] if s.get("publisher")]
    if not any(marker in lowered for marker in ATTRIBUTION_MARKERS):
        problems.append("Narration never attributes the report to its sources.")
    if publishers and not any(_publisher_mentioned(name, spoken) for name in publishers):
        problems.append("Narration does not name any of the corroborating publishers.")
    evidence = " ".join(f"{s['title']} {s.get('text_snippet', '')}" for s in story["sources"])
    invented = sorted(_numbers(everything) - _numbers(evidence))
    if invented:
        problems.append("Script contains numbers that are not in the sources: " + ", ".join(invented[:6]))
    if SENSATIONAL.search(everything):
        problems.append("Script uses sensational wording.")
    sensitive = is_sensitive(everything, ignore=publishers)
    if sensitive:
        problems.append(f"Script touches a sensitive subject ('{sensitive}') that needs a human editor.")
    return problems


def _publisher_mentioned(name: str, text: str) -> bool:
    """True when the publisher's name (or its distinctive first word) is spoken."""
    name_l, text_l = name.lower().strip(), text.lower()
    if name_l and name_l in text_l:
        return True
    head = re.sub(r"^the\s+", "", name_l).split(" ")[0]
    return len(head) >= 3 and re.search(rf"(?<![a-z]){re.escape(head)}(?![a-z])", text_l) is not None


def template_script(story: dict) -> dict:
    """Model-free English script that only restates the corroborated headline."""
    sources = story["sources"]
    names = list(dict.fromkeys(s["publisher"] for s in sources if s.get("publisher")))
    first, second = names[0], (names[1] if len(names) > 1 else names[0])
    headline = story["headline"].rstrip(".")
    other = next((s["title"].rstrip(".") for s in sources[1:] if s["title"].rstrip(".") != headline), "")
    scenes = [
        {"say": f"According to {first}, {headline}.", "label": "What happened",
         "image": "a modern newsroom desk with screens showing abstract world map graphics",
         "keywords": ["newsroom", "world map"]},
        {"say": (f"{second} reports the same development: {other}." if other and second != first
                 else f"The report has also been carried by {second}, which describes the same development."),
         "label": "Second source",
         "image": "stacked newspapers and a tablet on a wooden table in soft daylight",
         "keywords": ["newspapers", "tablet reading"]},
        {"say": "These are early reports. More details are expected as the publishers update their coverage.",
         "label": "Developing",
         "image": "a city skyline at dawn with light traffic trails",
         "keywords": ["city skyline", "dawn"]},
        {"say": f"This is a developing report based on {first} and {second}. Follow {cfg.CHANNEL_NAME} for clear, sourced updates.",
         "label": "Sourced update",
         "image": "an abstract glowing network of connected points on a dark blue background",
         "keywords": ["network", "data"]},
    ]
    for scene in scenes:
        scene["caption"] = scene["say"]
    return {"title": headline[:80], "hook": headline[:50], "category": "news", "scenes": scenes}


def build_script(story: dict, language: str = "english") -> dict:
    """Write and validate the script; fails closed when it cannot be grounded."""
    language = language if language in _LANGUAGE_RULES else "english"
    attempts = max(1, int(os.getenv("NEWS_SCRIPT_ATTEMPTS", "2") or 2))
    prompt = NEWS_PROMPT.format(channel=cfg.CHANNEL_NAME, language_rule=_LANGUAGE_RULES[language],
                                first_publisher=story["sources"][0]["publisher"],
                                evidence=_evidence_text(story))
    request, last = prompt, "no attempt made"
    script = None
    try:
        from core.script_engine import _ask_gemini, _parse
        for _ in range(attempts):
            try:
                candidate = _parse(_ask_gemini(request))
            except ValueError as exc:
                last = f"invalid JSON: {exc}"
                request = prompt + "\n\nYour previous answer was not valid JSON. Return ONLY the JSON object."
                continue
            problems = validate_news_script(candidate, story, language)
            if not problems:
                script = candidate
                break
            last = "; ".join(problems)
            print(f"News script rejected: {last}")
            request = (prompt + "\n\nYour previous answer was rejected: " + last
                       + ". Fix every problem using ONLY the sources and return ONLY the corrected JSON.")
    except Exception as exc:  # no model configured / every provider down
        last = f"{type(exc).__name__}: {str(exc)[:200]}"
    if script is None:
        if os.getenv("NEWS_TEMPLATE_FALLBACK", "").strip().lower() in {"1", "true", "yes"}:
            script = template_script(story)
            language = "english"
            problems = validate_news_script(script, story, language)
            if problems:
                raise NoPublishableStory("Template script failed grounding checks: " + "; ".join(problems))
            print("News script: using the model-free attributed template.")
        else:
            raise NoPublishableStory(f"No grounded news script could be written ({last}).")
    script["format"] = KIND
    script["category"] = "news"
    script["language"] = language
    script["sources"] = [{"title": s["title"], "publisher": s["publisher"],
                          "url": s.get("link") or s.get("url"), "published": s.get("published"),
                          "tier": s.get("tier")} for s in story["sources"]]
    script["description"] = (
        f"{script.get('title', story['headline'])}\n\nA short, sourced news summary. "
        "Visuals are illustrative and do not show the actual event. "
        "This report is based only on the publishers listed below and may be updated as they report more.")
    return script


# --- orchestration ----------------------------------------------------------

def run(language: str | None = None, upload: bool = False, candidates: list[dict] | None = None,
        output_dir: Path | None = None) -> dict:
    """Produce at most one news Short. Returns a JSON-safe result describing what happened."""
    from core.daily_news_pipeline import collect_candidates, load_seen, mark_seen, save_json
    from core.db import save_video
    from core.pipeline import build_shorts

    language = str(language or os.getenv("NEWS_LANGUAGE", "english")).strip().lower()
    output_dir = Path(output_dir or cfg.OUTPUT_DIR / "daily_news")
    state_dir = cfg.STATE_DIR / "daily_news"
    feed_errors = []
    if candidates is None:
        collected = collect_candidates()
        candidates, feed_errors = collected["candidates"], collected.get("feed_errors", [])
    seen = load_seen(state_dir / "seen.json")
    fresh = [item for item in candidates if item.get("id") not in seen]
    ledger = dedupe.Ledger()
    story, audit = select_story(fresh, ledger)
    selection = {"generated_at": _now().isoformat(), "candidate_count": len(fresh),
                 "feed_errors": feed_errors, "clusters_considered": audit[:60],
                 "selected": story and {"headline": story["headline"], "reason": story["reason"],
                                        "sources": story["sources"]}}
    save_json(output_dir / "auto_selection.json", selection)
    if not story:
        return {"status": "no_story", "candidate_count": len(fresh),
                "reason": "No story was corroborated by eligible independent sources and safe to automate."}

    script = build_script(story, language)
    language = script.get("language", language)
    job_id = save_video(script["title"], KIND, language, "news", script, pipeline="daily_news_auto",
                        topic_key=dedupe.topic_key(story["headline"]),
                        source_ref=",".join(story["source_refs"][:20]))
    build_dir = cfg.BUILD_DIR / f"job_{job_id}"
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "verification.json").write_text(json.dumps({
        "status": "PASS", "method": "deterministic source-tier and independence gate",
        "gate": story["gate"], "sources": story["sources"], "checked_at": _now().isoformat(),
        "notice": "Automated corroboration of headlines only. Nothing is uploaded until a human has reviewed the package.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    video = build_shorts(script, job_id, language=language)
    # Record immediately so a later failure cannot cause the same story to be rebuilt.
    ledger.record(KIND, script["title"], topic=story["headline"], source_refs=story["source_refs"],
                  job_id=job_id, status="built")
    mark_seen(story["source_refs"], state_dir)
    result = {"status": "built", "job_id": job_id, "video": str(video), "title": script["title"],
              "sources": [s["publisher"] for s in story["sources"]]}
    if upload:
        from core.publish import DuplicateUpload, publish_job
        from core.release_gate import ReleaseBlocked
        try:
            result["upload"] = publish_job(job_id, wait=True)
            result["status"] = "uploaded"
        except (ReleaseBlocked, DuplicateUpload) as exc:
            # The video exists and is kept for review; it is simply not uploaded.
            # A new video always stops here: it has no human review yet.
            result["status"] = "upload_blocked"
            result["upload_blocked_reason"] = str(exc)
            try:
                from core.db import get_video
                from core.local_review import build_package
                result["review_package"] = build_package(get_video(job_id)).get("package_dir")
            except Exception as review_exc:      # the package is a convenience here, never a failure
                result["review_package_error"] = str(review_exc)[:300]
    return result
