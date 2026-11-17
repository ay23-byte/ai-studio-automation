"""Openverse discovery for openly licensed documentary stills.

Anonymous API access is free. This adapter deliberately accepts only licences
compatible with commercial YouTube use (CC0, public domain, CC BY, CC BY-SA).
Search results are leads, not proof of historical authenticity or rights.
"""
from __future__ import annotations

import json
import re
import time
import threading
from datetime import date
from pathlib import Path

import requests
from core.visual_consistency import MIN_SOURCE_WIDTH

API = "https://api.openverse.org/v1/images/"
USER_AGENT = "AIStudioDocumentary/1.0 (openly licensed documentary image research)"
TOKEN_API = "https://api.openverse.org/v1/auth_tokens/token/"
_TOKEN = {"value": "", "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()
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


def _search_query(value: str) -> str:
    """Turn a narration instruction into a short subject query, not a paragraph."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    # Prefer an explicit visual description; otherwise use the spoken line rather
    # than the renderer's surrounding instruction template.
    visual = re.search(r"(?:visual idea|visual direction)\s*:\s*(.+?)(?=\s+(?:art direction|spoken line)\s*:|\s+9:16\b|$)", text, re.I)
    spoken = re.search(r"spoken line\s*:\s*(.+)$", text, re.I)
    if visual:
        text = visual.group(1)
    elif spoken:
        text = spoken.group(1)
    text = re.sub(r"^(?:according to|as reported by|the report says|reports that)\s+", "", text, flags=re.I)
    text = re.sub(r"\b(?:noted|reported|reports|says|said)\b", " ", text, flags=re.I)
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    stop = {"the", "and", "for", "with", "from", "that", "this", "according", "noted",
            "reported", "reports", "says", "said", "news", "today", "yesterday", "vertical"}
    words = [w for w in words if len(w) > 2 and w.lower() not in stop]
    # 8-10 meaningful terms is enough for a broad still search and keeps the
    # relevance denominator from being diluted by narration filler.
    return " ".join(words[:10])[:120]


def _access_token() -> str:
    """Get/refresh the optional Openverse OAuth token using client credentials."""
    import os
    client_id = os.getenv("OPENVERSE_CLIENT_ID", "").strip()
    client_secret = os.getenv("OPENVERSE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return ""
    now = time.monotonic()
    with _TOKEN_LOCK:
        if _TOKEN["value"] and _TOKEN["expires_at"] > now + 60:
            return str(_TOKEN["value"])
        try:
            response = requests.post(
                TOKEN_API,
                data={"grant_type": "client_credentials", "client_id": client_id,
                      "client_secret": client_secret},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload.get("access_token") or "")
            if not token:
                raise ValueError("Openverse token response omitted access_token")
            _TOKEN.update(value=token, expires_at=now + max(60, int(payload.get("expires_in") or 36000)))
            return token
        except (requests.RequestException, ValueError, TypeError) as exc:
            print(f"  Openverse OAuth unavailable: {type(exc).__name__}: {exc}")
            _TOKEN.update(value="", expires_at=0.0)
            return ""


def _search(query: str) -> list[dict]:
    last_error = None
    token = _access_token()
    for attempt in range(3):
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = requests.get(
                API,
                params={
                    "q": query,
                    "license": "cc0,pdm,by,by-sa",
                    "license_type": "commercial",
                    "filter_dead": "true",
                    "page_size": 20,
                },
                headers=headers,
                timeout=35,
            )
            if response.status_code == 401 and token and attempt == 0:
                # A rejected bearer token must not disable Openverse entirely:
                # its public API also supports anonymous searches. Retry once
                # without the stale/invalid token, then retain normal fail-closed
                # handling if anonymous access is unavailable on this runner.
                print("  Openverse bearer token was rejected; retrying once without authentication.")
                _TOKEN.update(value="", expires_at=0.0)
                token = ""
                continue
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
    # Normalize the full narration/creative prompt into a concise subject query.
    # Passing "Create a visual..." to Openverse produced long, poor API searches
    # and prevented relevant openly licensed stills from being discovered.
    query = _search_query(str(query or ""))
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
            from core.downloads import fetch
            fetch(item["url"], temp, kind="image", headers={"User-Agent": USER_AGENT})
            with Image.open(temp) as image:
                image.verify()
            with Image.open(temp) as image:
                width, height = image.size
                # Not below the visual audit's floor: a narrower still would fail the finished film.
                if width < MIN_SOURCE_WIDTH or height < 400:
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
