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
from datetime import date

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


def _video_dimensions(path):
    try:
        raw = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(path)
        ], stderr=subprocess.DEVNULL)
        stream = (json.loads(raw).get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except Exception:
        return 0, 0


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
        width, height = _video_dimensions(path)
        metadata["width"], metadata["height"] = width, height
        from core.asset_licensing import score_asset
        score = score_asset(metadata, width, height)
        metadata["quality_score"] = score["score_0_10"]
        metadata["quality_score_details"] = score
        if score["score_0_10"] < 7.0:
            path.unlink(missing_ok=True)
            print(f"  archive candidate rejected: score {score['score_0_10']}/10 is below 7")
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
        rights_text = " ".join(str(meta.get(key) or "") for key in ("description", "keywords", "center"))
        # Do not infer public-domain status from NASA hosting alone.
        if url and re.search(r"public domain", rights_text, re.I):
            results.append((url, {
                "provider": "NASA", "asset_id": meta.get("nasa_id"),
                "title": meta.get("title"), "page_url": page,
                "licence": "Public Domain",
                "licence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                "query": query, "licence_status": "Public-domain statement found in item metadata; inspect third-party credits"
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
        licence = val("LicenseShortName")
        licence_url = val("LicenseUrl")
        if not licence or not licence_url:
            continue
        normalized = licence.lower().replace("-", " ").strip()
        if not (normalized.startswith("cc0") or normalized.startswith("public domain")
                or normalized.startswith("cc by") or normalized.startswith("cc by sa")):
            continue
        creator = val("Artist")
        title = page.get("title")
        page_url = info.get("descriptionurl")
        results.append((url, {
            "provider": "Wikimedia Commons", "asset_id": title,
            "title": title, "page_url": page_url,
            "creator": creator, "licence": licence,
            "licence_url": licence_url, "query": query,
            "attribution_text": f"{creator or 'Author not listed'}, {title}, {licence}, {page_url}",
            "date_accessed": date.today().isoformat(),
            "licence_status": "File-specific licence metadata found; verify attribution and restrictions"
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
        item_meta = (meta or {}).get("metadata", {})
        rights = str(item_meta.get("rights") or "")
        licence_url = str(item_meta.get("licenseurl") or item_meta.get("license_url") or "")
        licence = str(item_meta.get("license") or "")
        normalized = (licence + " " + rights).lower()
        licence_url_lower = licence_url.lower()
        if "public domain" in normalized:
            licence = "Public Domain"
        elif "creativecommons.org/publicdomain/zero" in licence_url_lower:
            licence = "CC0 1.0"
        elif "creativecommons.org/licenses/by-sa/" in licence_url_lower:
            licence = "CC BY-SA 4.0"
        elif "creativecommons.org/licenses/by/" in licence_url_lower:
            licence = "CC BY 4.0"
        else:
            continue
        candidates = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4")
                      and int(f.get("size") or 0) > 50000]
        candidates.sort(key=lambda f: int(f.get("size") or 0), reverse=True)
        if candidates:
            results.append((f"https://archive.org/download/{quote(identifier)}/{quote(candidates[0]['name'])}", {
                "provider": "Internet Archive", "asset_id": identifier,
                "title": doc.get("title"), "page_url": f"https://archive.org/details/{identifier}",
                "licence": licence or rights, "licence_url": licence_url,
                "query": query, "date_accessed": date.today().isoformat(),
                "attribution_text": f"{doc.get('title')} — Internet Archive item {identifier}; {licence or rights}; https://archive.org/details/{identifier}",
                "licence_status": "Explicit item-level rights and licence URL found; verify restrictions"
            }))
    return results


def _loc(query):
    """Search Library of Congress video collections and accept only explicit rights metadata."""
    data = _get_json("https://www.loc.gov/videos/", {
        "fo": "json", "q": query, "c": 10,
    })
    results = []
    for item in (data or {}).get("results", []):
        rights = str(item.get("rights") or item.get("rights_advisory") or "")
        rights_low = rights.lower()
        if not any(term in rights_low for term in (
            "public domain", "no known restrictions", "no known copyright restrictions"
        )):
            continue
        resources = item.get("resources") or []
        urls = []
        for resource in resources:
            if isinstance(resource, dict):
                urls.extend([resource.get("url"), resource.get("video")])
                for file_item in resource.get("files", []) if isinstance(resource.get("files"), list) else []:
                    if isinstance(file_item, dict):
                        urls.append(file_item.get("url"))
        url = next((u for u in urls if isinstance(u, str) and
                    u.lower().split("?")[0].endswith((".mp4", ".mov", ".webm"))), None)
        if not url:
            continue
        page = str(item.get("id") or item.get("url") or "")
        if page.startswith("/"):
            page = "https://www.loc.gov" + page
        results.append((url, {
            "provider": "Library of Congress",
            "asset_id": item.get("id") or page,
            "title": item.get("title"),
            "page_url": page,
            "licence": "Public Domain" if "public domain" in rights_low else "No known copyright restrictions",
            "licence_url": page,
            "rights_advisory": rights,
            "query": query,
            "date_accessed": date.today().isoformat(),
            "attribution_text": f"{item.get('title', 'Untitled')} — Library of Congress; {rights}; {page}",
            "licence_status": "LOC item-level rights advisory found; verify item notes and third-party restrictions",
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
    providers = ((_nasa, _loc, _wikimedia, _internet_archive) if is_space
                 else (_loc, _wikimedia, _internet_archive))
    candidate_count = 0
    for query in _expanded_queries(keywords, scene_prompt)[:3]:
        candidates = []
        for provider in providers:
            candidates.extend(provider(query))
        ranked = sorted(candidates, key=lambda item: _metadata_relevance(
            query + " " + scene_prompt, item[1]), reverse=True)[:5]
        for url, metadata in ranked:
            if candidate_count >= 5:
                return False
            candidate_count += 1
            score = _metadata_relevance(query + " " + scene_prompt, metadata)
            metadata["relevance_score"] = round(score, 3)
            metadata["scene_prompt"] = str(scene_prompt)[:500]
            metadata["date_accessed"] = metadata.get("date_accessed") or date.today().isoformat()
            licence = str(metadata.get("licence") or "").lower()
            licence_url = str(metadata.get("licence_url") or "")
            known = bool(licence and licence_url and (
                "public domain" in licence or licence.startswith("cc0")
                or licence.startswith("cc by") or licence.startswith("cc by-sa")
            ))
            if not known or score < 0.25:
                continue
            identity = f"{metadata.get('provider')}:{metadata.get('asset_id') or url}"
            if identity in used_ids:
                continue
            if _download(url, path, metadata):
                used_ids.add(identity)
                return True
    return False
