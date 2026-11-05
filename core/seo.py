"""YouTube SEO package generation and safe metadata validation.

SEO suggestions are model-generated editorial suggestions, not live keyword-volume
or trend research. Long-form chapter times are estimates derived from section
word counts and the script's estimated runtime; review them against the final edit.
"""
import re
from core.script_engine import _ask_gemini, _parse


def _clean_list(values, limit):
    result, seen, total = [], set(), 0
    for value in values if isinstance(values, list) else []:
        item = re.sub(r"\s+", " ", str(value or "")).strip().strip("#")
        key = item.casefold()
        if not item or key in seen:
            continue
        if len(result) >= limit:
            break
        if total + len(item) + (1 if result else 0) > 500:
            break
        result.append(item)
        seen.add(key)
        total += len(item) + (1 if len(result) > 1 else 0)
    return result


def _timestamp(seconds):
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def estimate_chapters(script):
    """Create clearly labelled approximate chapter starts from narration word counts."""
    if str(script.get("format", "")).lower() == "shorts":
        return []
    sections = script.get("sections") or []
    if len(sections) < 2:
        return []
    weights = [max(1, len(str(section.get("narration") or "").split())) for section in sections]
    total_words = sum(weights)
    try:
        runtime = float(script.get("estimated_minutes") or script.get("duration_minutes") or 0) * 60
    except (TypeError, ValueError):
        runtime = 0
    if runtime <= 0:
        return []
    chapters, elapsed = [], 0.0
    for index, (section, weight) in enumerate(zip(sections, weights)):
        if index:
            chapters.append({"timestamp": _timestamp(elapsed), "title": str(section.get("title") or f"Chapter {index + 1}")[:100]})
        elapsed += runtime * weight / total_words
    if len(chapters) < 2:
        return []
    return [{"timestamp": "00:00", "title": str(sections[0].get("title") or "Introduction")[:100]}] + chapters


def generate_seo_package(script, channel_name=""):
    """Generate SEO copy while deriving chapter timing locally, never from AI guesses."""
    if not isinstance(script, dict) or not script.get("title"):
        raise ValueError("A script with a title is required to generate SEO metadata.")
    compact = {
        "title": script.get("title", ""),
        "category": script.get("category", ""),
        "format": script.get("format", ""),
        "description": script.get("description", ""),
        "tags": script.get("tags", []),
        "hook": script.get("hook", ""),
        "sections": [
            {"title": s.get("title", ""), "narration": str(s.get("narration", ""))[:3500]}
            for s in (script.get("sections") or [])
        ],
        "scenes": [
            {"say": s.get("say", ""), "caption": s.get("caption", "")}
            for s in (script.get("scenes") or [])
        ],
    }
    prompt = f"""Act as a careful YouTube SEO editor. Create useful, natural metadata for this video.
CHANNEL: {channel_name or "Not specified"}
SCRIPT CONTEXT (source of truth):
{__import__("json").dumps(compact, ensure_ascii=False)[:18000]}

Return ONLY a JSON object:
{{"titles":["3 distinct title options"],"description":"Ready-to-paste description with a concise opening, useful summary and natural CTA","tags":["relevant search phrase"],"hashtags":["#RelevantTopic"],"pinned_comment":"A specific question that invites genuine discussion","thumbnail_text":["short option one","short option two","short option three"]}}

Rules:
- Titles must be accurate, compelling, natural and under 100 characters. Avoid fake urgency and misleading clickbait.
- Description must describe only content actually covered. Include a clear CTA, but no invented links, sources, timestamps or claims.
- Provide relevant tags, not keyword stuffing or unrelated trending terms.
- Return 3-5 hashtags at most; use relevant topic terms only.
- Thumbnail text should be 2-5 words, readable and consistent with the script.
- Do not invent keyword search volume, rankings, trend status, statistics, sources or URLs.
- For Shorts, keep metadata concise. Do not create chapters or timestamps.
"""
    raw = _parse(_ask_gemini(prompt))
    titles = []
    for value in raw.get("titles", []) if isinstance(raw.get("titles"), list) else []:
        title = re.sub(r"\s+", " ", str(value or "")).strip()[:100]
        if title and title.casefold() not in {x.casefold() for x in titles}:
            titles.append(title)
        if len(titles) == 5:
            break
    if not titles:
        titles = [str(script["title"])[:100]]
    tags = _clean_list(raw.get("tags") or script.get("tags") or [], 30)
    hashtags = ["#" + x for x in _clean_list(raw.get("hashtags") or [], 3)]
    chapters = estimate_chapters(script)
    return {
        "titles": titles,
        "description": str(raw.get("description") or script.get("description") or "")[:5000],
        "tags": tags,
        "hashtags": hashtags,
        "pinned_comment": str(raw.get("pinned_comment") or "")[:1000],
        "thumbnail_text": [str(x).strip()[:50] for x in (raw.get("thumbnail_text") or [])[:5] if str(x).strip()],
        "chapters": chapters,
        "chapters_notice": (
            "Chapter times are estimates based on section narration word counts and the script's estimated runtime. "
            "Check and adjust them against the rendered video before publishing."
            if chapters else ("Shorts do not use chapters." if str(script.get("format", "")).lower() == "shorts"
                              else "No chapter estimates available: the script needs multiple sections and an estimated runtime.")
        ),
        "keyword_research_note": "These are AI-generated metadata suggestions, not live search-volume, ranking or trend measurements.",
    }


def normalize_seo_package(package):
    """Apply YouTube-safe character/count limits to edited metadata."""
    package = dict(package or {})
    titles = [str(x).strip()[:100] for x in package.get("titles", []) if str(x).strip()]
    package["titles"] = titles[:5]
    package["description"] = str(package.get("description") or "")[:5000]
    package["tags"] = _clean_list(package.get("tags") or [], 30)
    package["hashtags"] = ["#" + x for x in _clean_list(package.get("hashtags") or [], 3)]
    package["pinned_comment"] = str(package.get("pinned_comment") or "")[:1000]
    package["thumbnail_text"] = [str(x).strip()[:50] for x in package.get("thumbnail_text", []) if str(x).strip()][:5]
    clean_chapters, previous = [], -1
    for chapter in package.get("chapters", []):
        stamp = str(chapter.get("timestamp") or "").strip()
        title = str(chapter.get("title") or "").strip()[:100]
        match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", stamp)
        if not match or not title:
            continue
        parts = [int(x) for x in match.groups() if x is not None]
        seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        if seconds <= previous:
            continue
        clean_chapters.append({"timestamp": _timestamp(seconds), "title": title})
        previous = seconds
    if clean_chapters and clean_chapters[0]["timestamp"] != "00:00":
        clean_chapters.insert(0, {"timestamp": "00:00", "title": "Introduction"})
    package["chapters"] = clean_chapters
    return package
