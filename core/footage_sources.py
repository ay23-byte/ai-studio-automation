"""Free-first documentary footage discovery from public media libraries.

NASA, Wikimedia Commons and Internet Archive
are searched without an API key or paid account. Every asset is validated with ffprobe and
a JSON provenance sidecar is written beside the downloaded clip. Search results
are leads: review the asset-specific licence/credit before publishing.
"""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests

TIMEOUT = 35
USER_AGENT = "AIStudioDocumentary/1.0 (educational video research)"


def _get_json(url, params=None, headers=None):
    try:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        if response.status_code == 429:
            return None
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"  footage discovery unavailable ({url.split('/')[2]}): {exc}")
        return None


def _valid_video(path):
    try:
        if path.stat().st_size < 50000:
            return False
        raw = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)], stderr=subprocess.DEVNULL
        )
        info = json.loads(raw)
        duration = float((info.get("format") or {}).get("duration") or 0)
        stream = next((s for s in info.get("streams", [])
                       if s.get("codec_type") == "video"), {})
        return duration >= 2 and int(stream.get("width") or 0) >= 256 and int(stream.get("height") or 0) >= 256
    except Exception:
        return False


def _download(url, path, metadata):
    if not url or not str(url).startswith("https://"):
        return False
    try:
        with requests.get(url, stream=True, timeout=90,
                          headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            with path.open("wb") as target:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        target.write(chunk)
        if not _valid_video(path):
            path.unlink(missing_ok=True)
            return False
        sidecar = path.with_suffix(path.suffix + ".source.json")
        sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  clip ok ({metadata.get('provider')}); licence review required")
        return True
    except Exception as exc:
        path.unlink(missing_ok=True)
        print(f"  footage download failed: {exc}")
        return False


def _nasa(query):
    data = _get_json("https://images-api.nasa.gov/search",
                     {"q": query, "media_type": "video", "page_size": 8})
    results = []
    for item in (data or {}).get("collection", {}).get("items", []):
        meta = (item.get("data") or [{}])[0]
        links = item.get("links") or []
        page = next((x.get("href") for x in links if x.get("rel") == "canonical"), "")
        href = item.get("href")
        manifest = _get_json(href) if href else None
        candidates = manifest if isinstance(manifest, list) else []
        url = next((x for x in candidates if isinstance(x, str) and
                    x.lower().endswith((".mp4", ".mov")) and "~" not in x), None)
        if url:
            results.append((url, {
                "provider": "NASA", "asset_id": meta.get("nasa_id"),
                "title": meta.get("title"), "page_url": page,
                "licence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                "query": query, "licence_status": "Check NASA media usage guidance and third-party credits"
            }))
    return results


def _wikimedia(query):
    data = _get_json("https://commons.wikimedia.org/w/api.php", {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:video {query}", "gsrnamespace": 6,
        "gsrlimit": 10, "prop": "imageinfo", "iiprop": "url|extmetadata"
    }, {"User-Agent": USER_AGENT})
    pages = (data or {}).get("query", {}).get("pages", {})
    results = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("url", "")
        if not url.lower().endswith((".webm", ".ogv", ".mp4")):
            continue
        ext = info.get("extmetadata") or {}
        def val(key):
            return re.sub("<[^>]+>", "", str((ext.get(key) or {}).get("value") or ""))
        results.append((url, {
            "provider": "Wikimedia Commons", "asset_id": page.get("title"),
            "title": page.get("title"), "page_url": info.get("descriptionurl"),
            "creator": val("Artist"), "licence": val("LicenseShortName"),
            "licence_url": val("LicenseUrl"), "query": query,
            "licence_status": "Follow the file-specific licence and attribution"
        }))
    return results


def _internet_archive(query):
    data = _get_json("https://archive.org/advancedsearch.php", {
        "q": f'mediatype:movies AND ({query})', "fl[]": ["identifier", "title"],
        "rows": 8, "page": 1, "output": "json"
    })
    results = []
    docs = (data or {}).get("response", {}).get("docs", [])
    for doc in docs:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        meta = _get_json(f"https://archive.org/metadata/{quote(identifier)}")
        files = (meta or {}).get("files", [])
        candidates = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4")
                      and int(f.get("size") or 0) > 50000]
        candidates.sort(key=lambda f: int(f.get("size") or 0), reverse=True)
        if candidates:
            results.append((f"https://archive.org/download/{quote(identifier)}/{quote(candidates[0]['name'])}", {
                "provider": "Internet Archive", "asset_id": identifier,
                "title": doc.get("title"), "page_url": f"https://archive.org/details/{identifier}",
                "licence_url": f"https://archive.org/details/{identifier}",
                "query": query, "licence_status": "Check item metadata and rights statement; not all archive items are public domain"
            }))
    return results


def _tokens(value):
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "about",
            "scene", "footage", "video", "documentary", "file"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) > 2 and word not in stop}


def _metadata_relevance(query, metadata):
    wanted = _tokens(query)
    found = _tokens(metadata.get("title")) | _tokens(metadata.get("description"))
    return len(wanted & found) / len(wanted) if wanted else 0.0


def _expanded_queries(keywords, scene_prompt=""):
    values = []
    for item in keywords or []:
        query = str(item).strip()
        if query and query not in values:
            values.append(query)
    context_tokens = _tokens(" ".join(values) + " " + str(scene_prompt or ""))
    india_terms = {"india", "indian", "mughal", "maratha", "british", "delhi",
                   "mumbai", "bombay", "kolkata", "calcutta", "plassey",
                   "panipat", "1857", "independence", "east", "company"}
    if context_tokens & india_terms:
        for query in list(values):
            specific = f"{query} India"
            if specific not in values:
                values.append(specific)
    return values[:12]


def fetch_documentary_clip(keywords, path, used_ids=None, scene_prompt=""):
    """Search free archives with conservative metadata relevance ranking.

    Text matching is a discovery filter, not computer-vision verification.
    """
    used_ids = used_ids if used_ids is not None else set()
    context = " ".join(str(x) for x in (keywords or [])) + " " + str(scene_prompt or "")
    is_space = bool(_tokens(context) & {"space", "nasa", "planet", "mars", "moon", "astronaut", "rocket"})
    providers = ((_nasa, _wikimedia, _internet_archive) if is_space
                 else (_wikimedia, _internet_archive))
    for query in _expanded_queries(keywords, scene_prompt):
        for provider in providers:
            ranked = sorted(provider(query), key=lambda item: _metadata_relevance(
                query + " " + scene_prompt, item[1]), reverse=True)
            for url, metadata in ranked:
                score = _metadata_relevance(query, metadata)
                metadata["relevance_score"] = round(score, 3)
                metadata["scene_prompt"] = str(scene_prompt)[:500]
                if score < 0.12:
                    continue
                identity = f"{metadata.get('provider')}:{metadata.get('asset_id') or url}"
                if identity in used_ids:
                    continue
                if _download(url, path, metadata):
                    used_ids.add(identity)
                    return True
    return False
