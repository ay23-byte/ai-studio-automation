"""Rights-aware retrieval of genuine archival still images.

This module searches Wikimedia Commons for file-level image records. It only
downloads items with an explicit CC0, public-domain, CC BY or CC BY-SA
statement and writes an item-level provenance sidecar. Discovery metadata is
not proof of historical authenticity: human review remains mandatory.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from pathlib import Path

import requests

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AIStudioDocumentary/1.1 (archival image research)"
# Commons asks automated clients to identify themselves and avoid burst traffic.
# Cache successful metadata searches and serialize requests for the life of a build.
_SEARCH_CACHE: dict[str, list[dict]] = {}
_SEARCH_LOCK = threading.Lock()
_LAST_SEARCH_AT = 0.0
_MIN_SEARCH_INTERVAL_SECONDS = 1.0


def _retry_delay(headers: dict | None, attempt: int) -> float:
    """Respect Retry-After in either seconds or HTTP-date form."""
    raw = str((headers or {}).get("Retry-After") or "").strip()
    if raw:
        try:
            return min(30.0, max(1.0, float(raw)))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(30.0, max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    return min(20.0, 2.0 ** (attempt + 1))
_ALLOWED = re.compile(r"^(?:cc0|public domain|cc by(?:-sa)?(?:\s|$))", re.I)


def _plain(value: object) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _tokens(value: object) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "that", "this", "photo",
            "image", "archive", "archival", "historical", "photograph"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) > 2 and word not in stop}


def _relevance(query: str, title: str, description: str) -> float:
    wanted = _tokens(query)
    found = _tokens(title) | _tokens(description)
    return len(wanted & found) / len(wanted) if wanted else 0.0


def _search_query(value: str) -> str:
    """Extract factual subject/location/date, not the entire cinematic prompt."""
    text = str(value or "").strip()
    context = re.search(r"Specific context:\s*(.+?)(?:\.\s*(?:Prefer genuine|Use only|Do not label)|$)", text, re.I)
    if context:
        text = context.group(1)
    else:
        text = re.sub(r"^(?:Create a documentary shot.*?Narration anchor:|.*?Visual direction:)", "", text, flags=re.I)
    text = re.sub(r"\b(?:prefer genuine|historically grounded|photorealistic|premium|documentary|composition|natural lighting)\b.*$", "", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9 ,;()'/-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .;,:-")
    # Commons search is more useful with the concrete subject and date.
    if len(text) > 180:
        text = text[:180].rsplit(" ", 1)[0]
    return text


def _search(query: str) -> list[dict]:
    global _LAST_SEARCH_AT
    cache_key = re.sub(r"\s+", " ", query).strip().casefold()
    with _SEARCH_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if cached is not None:
            return cached
        wait = _MIN_SEARCH_INTERVAL_SECONDS - (time.monotonic() - _LAST_SEARCH_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_SEARCH_AT = time.monotonic()
    response = requests.get(
        API,
        params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
            "gsrlimit": 30, "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=35,
    )
    response.raise_for_status()
    payload = response.json()
    pages = (payload.get("query") or {}).get("pages") or {}
    result = list(pages.values()) if isinstance(pages, dict) else []
    with _SEARCH_LOCK:
        _SEARCH_CACHE[cache_key] = result
    return result


def fetch_archival_image(query: str, path: str | Path,
                         used_ids: set[str] | None = None,
                         min_relevance: float = 0.30) -> bool:
    """Download one relevant, explicitly licensed Commons image.

    Returns False when no qualifying image is found. It never substitutes an
    AI-generated image for an archival request.
    """
    query = _search_query(query)
    if not query:
        print("  archival image search skipped: no concise subject/location/date query was available.")
        return False
    used_ids = used_ids if used_ids is not None else set()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pages = None
    for attempt in range(3):
        try:
            pages = _search(query)
            break
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                delay = _retry_delay(
                    exc.response.headers if exc.response is not None else {},
                    attempt,
                )
                print(f"  Wikimedia archival search transient HTTP {status}; retry {attempt + 1}/2 after {delay:g}s.")
                time.sleep(delay)
                continue
            print(f"  archival image search unavailable: HTTP {status or 'error'}; trying Openverse fallback.")
            from core.openverse_images import fetch_openverse_image
            return fetch_openverse_image(query, target, used_ids, min_relevance=min_relevance)
        except Exception as exc:
            print(f"  archival image search unavailable: {type(exc).__name__}: {exc}; trying Openverse fallback.")
            from core.openverse_images import fetch_openverse_image
            return fetch_openverse_image(query, target, used_ids, min_relevance=min_relevance)
    if pages is None:
        from core.openverse_images import fetch_openverse_image
        return fetch_openverse_image(query, target, used_ids, min_relevance=min_relevance)

    candidates = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata") or {}

        def field(name: str) -> str:
            value = (ext.get(name) or {}).get("value") if isinstance(ext.get(name), dict) else ""
            return _plain(value)

        licence = field("LicenseShortName")
        licence_url = field("LicenseUrl")
        if not licence or not licence_url or not _ALLOWED.search(licence):
            continue
        mime = str(info.get("mime") or "")
        if not mime.startswith("image/"):
            continue
        url = str(info.get("url") or "")
        page_url = str(info.get("descriptionurl") or "")
        if not url.startswith("https://") or not page_url.startswith("https://commons.wikimedia.org/"):
            continue
        title = str(page.get("title") or "")
        description = field("ImageDescription")
        creator = field("Artist")
        date_created = field("DateTimeOriginal") or field("DateTime")
        identity = str(page.get("pageid") or title)
        score = _relevance(query, title + " " + description, creator)
        if identity in used_ids or score < float(min_relevance):
            continue
        candidates.append((score, info, {
            "provider": "Wikimedia Commons",
            "asset_id": identity,
            "title": title,
            "description": description[:3000],
            "creator": creator,
            "date_created": date_created,
            "page_url": page_url,
            "licence": licence,
            "licence_url": licence_url,
            "query": query,
            "relevance_score": round(score, 3),
            "date_accessed": date.today().isoformat(),
            "asset_type": "archival_still_candidate",
            "archival_authenticity_status": "pending_human_verification",
            "attribution_text": f"{title} — {creator or 'Creator not listed'}; {licence}; {page_url}",
            "licence_status": "Explicit file-level Commons licence metadata found; inspect item page and restrictions",
            "human_visual_inspection_required": True,
            "human_rights_review_required": True,
        }))
    candidates.sort(key=lambda item: item[0], reverse=True)

    from PIL import Image
    for _score, info, metadata in candidates:
        temp = target.with_name(target.name + ".download")
        try:
            from core.downloads import fetch
            fetch(info["url"], temp, kind="image", headers={"User-Agent": USER_AGENT})
            with Image.open(temp) as image:
                image.verify()
            with Image.open(temp) as image:
                width, height = image.size
                if width < 600 or height < 400:
                    raise ValueError("archival image resolution is below 600x400")
                metadata["width"], metadata["height"] = width, height
            temp.replace(target)
            target.with_suffix(target.suffix + ".source.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            used_ids.add(metadata["asset_id"])
            print(f"  archival still candidate downloaded; human authenticity and rights review required")
            return True
        except Exception as exc:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            print(f"  archival image candidate rejected: {type(exc).__name__}: {exc}")
    # Wikimedia is the primary source; Openverse broadens discovery across
    # openly licensed collections when Commons has no suitable result.
    from core.openverse_images import fetch_openverse_image
    if fetch_openverse_image(query, target, used_ids, min_relevance=min_relevance):
        return True
    return False
