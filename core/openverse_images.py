"""Openverse discovery for openly licensed documentary stills.

Anonymous API access is free. This adapter deliberately accepts only licences
compatible with commercial YouTube use (CC0, public domain, CC BY, CC BY-SA).
Search results are leads, not proof of historical authenticity or rights.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

import requests

API = "https://api.openverse.org/v1/images/"
USER_AGENT = "AIStudioDocumentary/1.0 (openly licensed documentary image research)"
_ALLOWED = {"cc0", "pdm", "by", "by-sa"}


def _tokens(value: object) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "that", "this", "photo",
            "image", "images", "video", "footage", "documentary", "historical",
            "archive", "archival", "photograph", "picture"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) > 2 and word not in stop}


def _relevance(query: str, item: dict) -> float:
    wanted = _tokens(query)
    found = (_tokens(item.get("title")) | _tokens(item.get("creator")) |
             _tokens(item.get("tags")))
    return len(wanted & found) / len(wanted) if wanted else 0.0


def _license_label(item: dict) -> tuple[str, str] | None:
    code = str(item.get("license") or "").strip().lower()
    version = str(item.get("license_version") or "").strip()
    if code not in _ALLOWED:
        return None
    if code == "cc0":
        return "CC0 1.0", str(item.get("license_url") or "https://creativecommons.org/publicdomain/zero/1.0/")
    if code == "pdm":
        return "Public Domain Mark", str(item.get("license_url") or "https://creativecommons.org/publicdomain/mark/1.0/")
    if code == "by":
        version = version or "4.0"
        return f"CC BY {version}", str(item.get("license_url") or f"https://creativecommons.org/licenses/by/{version}/")
    version = version or "4.0"
    return f"CC BY-SA {version}", str(item.get("license_url") or f"https://creativecommons.org/licenses/by-sa/{version}/")


def _search(query: str) -> list[dict]:
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                API,
                params={
                    "q": query,
                    "license": "cc0,pdm,by,by-sa",
                    "license_type": "commercial",
                    "filter_dead": "true",
                    "page_size": 30,
                },
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=35,
            )
            if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                if attempt < 2:
                    retry = response.headers.get("Retry-After", "")
                    try:
                        delay = min(8.0, max(1.0, float(retry)))
                    except (TypeError, ValueError):
                        delay = float(attempt + 1)
                    time.sleep(delay)
                    continue
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results") or []
            return results if isinstance(results, list) else []
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
        except (ValueError, TypeError) as exc:
            last_error = exc
            break
    if last_error:
        print(f"  Openverse image search unavailable: {type(last_error).__name__}: {last_error}")
    return []


def fetch_openverse_image(query: str, path: str | Path,
                          used_ids: set[str] | None = None,
                          min_relevance: float = 0.25) -> bool:
    """Download one relevant Openverse image with item-level licence metadata."""
    query = re.sub(r"\\s+", " ", str(query or "")).strip()[:180]
    if not query:
        return False
    used_ids = used_ids if used_ids is not None else set()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = []
    for item in _search(query):
        if not isinstance(item, dict):
            continue
        licence = _license_label(item)
        if not licence:
            continue
        url = str(item.get("url") or "")
        page = str(item.get("foreign_landing_url") or "")
        identity = str(item.get("id") or page or url)
        if (not identity or identity in used_ids or not url.startswith("https://")
                or not page.startswith("https://")):
            continue
        score = _relevance(query, item)
        if score < float(min_relevance):
            continue
        candidates.append((score, item, licence, identity))
    candidates.sort(key=lambda value: value[0], reverse=True)

    from PIL import Image
    for score, item, licence, identity in candidates:
        temp = target.with_name(target.name + ".download")
        try:
            with requests.get(item["url"], stream=True, timeout=90,
                              headers={"User-Agent": USER_AGENT}) as response:
                response.raise_for_status()
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            with Image.open(temp) as image:
                image.verify()
            with Image.open(temp) as image:
                width, height = image.size
                if width < 600 or height < 400:
                    raise ValueError("Openverse image resolution is below 600x400")
            temp.replace(target)
            licence_name, licence_url = licence
            creator = str(item.get("creator") or "Creator not supplied")
            title = str(item.get("title") or "Untitled openly licensed image")
            metadata = {
                "provider": "Openverse",
                "asset_id": identity,
                "title": title,
                "creator": creator,
                "description": str(item.get("attribution") or "")[:2000],
                "page_url": item["foreign_landing_url"],
                "licence": licence_name,
                "licence_url": licence_url,
                "attribution_text": str(item.get("attribution") or
                                        f"{title} — {creator}; {licence_name}; {item['foreign_landing_url']}"),
                "date_accessed": date.today().isoformat(),
                "query": query,
                "relevance_score": round(score, 3),
                "width": width,
                "height": height,
                "asset_type": "openly_licensed_still_candidate",
                "archival_authenticity_status": "not_verified",
                "licence_status": "Openverse item-level licence metadata found; inspect original item page and licence terms",
                "human_visual_inspection_required": True,
                "human_rights_review_required": True,
            }
            target.with_suffix(target.suffix + ".source.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            used_ids.add(identity)
            print("  Openverse still candidate downloaded; visual authenticity and rights review required")
            return True
        except Exception as exc:
            temp.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            print(f"  Openverse image candidate rejected: {type(exc).__name__}: {exc}")
    return False
