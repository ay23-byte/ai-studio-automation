"""Direct Wikimedia Commons still-image fallback with item-level rights metadata.

This is intentionally independent of Openverse so image discovery can continue
when Openverse rejects requests from hosted runners. It accepts only CC0,
Public Domain, CC BY and CC BY-SA items and preserves the exact file-page record.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import requests
from PIL import Image

from core.visual_consistency import MIN_SOURCE_WIDTH

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AIStudioAutomation/1.0 (https://github.com/ay23-byte/ai-studio-automation; openly licensed media research)"


def _query(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    visual = re.search(r"visual idea\s*:\s*(.+?)(?=\s+(?:art direction|spoken line)\s*:|$)", text, re.I)
    spoken = re.search(r"spoken line\s*:\s*(.+)$", text, re.I)
    if visual:
        text = visual.group(1)
    elif spoken:
        text = spoken.group(1)
    text = re.sub(r"^(?:according to|as reported by)\s+", "", text, flags=re.I)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    stop = {"the", "and", "for", "with", "from", "that", "this", "according",
            "noted", "reported", "reports", "says", "said", "news", "today"}
    return " ".join(w for w in words if len(w) > 2 and w.lower() not in stop)[:120]


def _ext(info: dict, key: str) -> str:
    item = (info.get("extmetadata") or {}).get(key) or {}
    value = item.get("value") if isinstance(item, dict) else ""
    return re.sub(r"<[^>]+>", " ", str(value or "")).strip()


def _licence(info: dict) -> tuple[str, str] | None:
    label = _ext(info, "LicenseShortName")
    url = _ext(info, "LicenseUrl")
    normalized = label.lower().replace("-", " ").strip()
    if "cc0" in normalized or "cc zero" in normalized:
        return "CC0 1.0", url or "https://creativecommons.org/publicdomain/zero/1.0/"
    if "public domain" in normalized:
        return "Public Domain", url or "https://creativecommons.org/publicdomain/mark/1.0/"
    if "cc by sa" in normalized:
        return (label, url) if url else None
    if re.search(r"cc by(?:\s|$)", normalized):
        return (label, url) if url else None
    return None


def fetch_wikimedia_image(query: str, path: str | Path, min_relevance: float = 0.25) -> bool:
    query = _query(query)
    if not query:
        return False
    response = requests.get(
        API,
        params={
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6, "gsrlimit": 30,
            "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1600,
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=(10, 30),
    )
    response.raise_for_status()
    pages = list((((response.json() or {}).get("query") or {}).get("pages") or {}).values())
    wanted = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2}
    candidates = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        licence = _licence(info)
        url = str(info.get("thumburl") or info.get("url") or "")
        page_url = str(info.get("descriptionurl") or "")
        mime = str(info.get("mime") or "").lower()
        if not licence or not url.startswith("https://") or not page_url.startswith("https://"):
            continue
        if mime and not mime.startswith("image/"):
            continue
        title = _ext(info, "ObjectName") or str(page.get("title") or "")
        creator = _ext(info, "Artist") or _ext(info, "Credit") or "Creator not supplied"
        tags = _ext(info, "Categories") + " " + _ext(info, "ImageDescription")
        found = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", " ".join([title, creator, tags])) if len(w) > 2}
        score = len(wanted & found) / len(wanted) if wanted else 0.0
        if score < float(min_relevance):
            continue
        candidates.append((score, page, info, licence, url, page_url, title, creator))
    candidates.sort(key=lambda row: row[0], reverse=True)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    from core.downloads import fetch
    for score, page, info, licence, url, page_url, title, creator in candidates[:8]:
        temp = target.with_name(target.name + ".download")
        try:
            fetch(url, temp, kind="image", headers={"User-Agent": USER_AGENT}, timeout=(10, 45))
            with Image.open(temp) as image:
                image.verify()
            with Image.open(temp) as image:
                width, height = image.size
                if width < MIN_SOURCE_WIDTH or height < 400:
                    raise ValueError("Wikimedia image is below 600x400")
            temp.replace(target)
            licence_name, licence_url = licence
            attribution = _ext(info, "Credit") or f"{title} — {creator}; {licence_name}; {page_url}"
            metadata = {
                "provider": "Wikimedia Commons", "asset_id": str(page.get("pageid") or page.get("title") or ""),
                "title": title, "creator": creator, "description": _ext(info, "ImageDescription"),
                "page_url": page_url, "download_url": url, "licence": licence_name,
                "licence_url": licence_url, "attribution_text": attribution,
                "date_accessed": date.today().isoformat(), "query": query,
                "relevance_score": round(score, 3), "width": width, "height": height,
                "asset_type": "openly_licensed_still_candidate",
                "human_visual_inspection_required": True, "human_rights_review_required": True,
                "restrictions": _ext(info, "Restrictions"),
            }
            target.with_suffix(target.suffix + ".source.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as exc:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            print(f"  Wikimedia image candidate rejected: {type(exc).__name__}: {exc}")
    return False
