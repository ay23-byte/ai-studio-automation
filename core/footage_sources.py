"""Free-first documentary footage discovery from public media libraries.

NASA, Wikimedia Commons and Internet Archive
are searched without an API key or paid account. Every asset is validated with ffprobe and
a JSON provenance sidecar is written beside the downloaded clip. Search results
are leads: review the asset-specific licence/credit before publishing.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests
from datetime import date

TIMEOUT = 35
# Authorization failures are stable for the lifetime of one production run.
_DISABLED_PROVIDER_HOSTS = set()
USER_AGENT = "AIStudioDocumentary/1.0 (educational video research)"


def _get_json(url, params=None, headers=None):
    """Fetch archive metadata with bounded retries and per-run provider circuit breaking.

    Rate limits and transient upstream failures are retried briefly. A provider
    that remains unavailable is skipped for the rest of this run so topic
    expansion cannot hammer an already failing API.
    """
    host = url.split("/")[2].lower() if "://" in url else ""
    if host in _DISABLED_PROVIDER_HOSTS:
        return None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    for attempt in range(3):
        try:
            response = requests.get(
                url, params=params, headers=request_headers, timeout=TIMEOUT
            )
            status = response.status_code
            if status in (401, 403):
                _DISABLED_PROVIDER_HOSTS.add(host)
                print(f"  footage provider disabled for this run ({host}): HTTP {status}; check API access/credentials.")
                return None
            if status == 429 or status in (500, 502, 503, 504):
                if attempt < 2:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(8.0, max(0.5, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = float(attempt + 1)
                    print(f"  footage provider transient HTTP {status} ({host}); retry {attempt + 1}/2 after {delay:g}s.")
                    time.sleep(delay)
                    continue
                _DISABLED_PROVIDER_HOSTS.add(host)
                print(f"  footage provider unavailable for rest of this run ({host}): HTTP {status} after retries.")
                return None
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                preview = " ".join(response.text[:180].split())
                print(f"  footage discovery unavailable ({host}): expected JSON, received {content_type or 'unknown content type'}; response begins: {preview!r}")
                return None
            try:
                return response.json()
            except ValueError as exc:
                print(f"  footage discovery unavailable ({host}): invalid JSON response ({exc}).")
                return None
        except requests.RequestException as exc:
            if attempt < 2:
                delay = float(attempt + 1)
                print(f"  footage provider request failed ({host}): {type(exc).__name__}; retry {attempt + 1}/2 after {delay:g}s.")
                time.sleep(delay)
                continue
            print(f"  footage discovery unavailable ({host or 'unknown host'}): {type(exc).__name__}: {exc}")
            return None
        except Exception as exc:
            print(f"  footage discovery unavailable ({host or 'unknown host'}): {type(exc).__name__}: {exc}")
            return None
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
        if ".m3u8" in str(url).lower():
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(url),
                "-c", "copy", "-bsf:a", "aac_adtstoasc", str(path)
            ], check=True, timeout=180)
        else:
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
        if score["score_0_10"] < 3.0:
            path.unlink(missing_ok=True)
            print(f"  archive candidate rejected: score {score['score_0_10']}/10 is below 3")
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
        # NASA manifests commonly expose the best downloadable rendition as
        # "~orig.mp4" (or "~large.mp4"). Do not discard those valid files.
        media_candidates = [x for x in candidates if isinstance(x, str) and
                            x.lower().endswith((".mp4", ".mov")) and
                            not re.search(r"~(?:thumb|small|preview)\.(?:mp4|mov)$", x, re.I)]
        media_candidates.sort(key=lambda x: (
            0 if re.search(r"~orig\.(?:mp4|mov)$", x, re.I) else
            1 if re.search(r"~large\.(?:mp4|mov)$", x, re.I) else
            2 if re.search(r"~medium\.(?:mp4|mov)$", x, re.I) else 3
        ))
        url = media_candidates[0] if media_candidates else None
        rights_text = " ".join(str(meta.get(key) or "") for key in ("description", "keywords", "center"))
        # NASA says its own media is generally not copyrighted, but third-party
        # material may appear on NASA sites. Require an identifiable NASA center
        # and reject explicit copyright/third-party credit signals. This remains
        # pending human review; the policy URL is recorded in every sidecar.
        center = str(meta.get("center") or "").strip()
        third_party_markers = re.search(
            r"copyright|\u00a9|\u00a9|courtesy of|provided by|licensed from|"
            r"\b(getty|ap|associated press|reuters|bbc|universal|disney)\b",
            rights_text, re.I
        )
        nasa_owned_item = bool(center) and not third_party_markers
        explicit_public_domain = bool(re.search(r"public domain", rights_text, re.I))
        if url and (explicit_public_domain or nasa_owned_item):
            licence = "Public Domain" if explicit_public_domain else "NASA Media Usage Guidelines"
            results.append((url, {
                "provider": "NASA", "asset_id": meta.get("nasa_id"),
                "title": meta.get("title"), "creator": f"NASA {center}".strip(),
                "page_url": page,
                "licence": licence,
                "licence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                "query": query,
                "rights_statement": "NASA media usage guidelines: NASA content is generally not subject to U.S. copyright unless third-party copyright is indicated; credit NASA and inspect all embedded third-party material.",
                "licence_status": "NASA center metadata present and no third-party/copyright marker detected; human review required"
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
        candidates = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4")                      and int(f.get("size") or 0) > 50000]
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




def _nara(query):
    """Search NARA Catalog v2 and accept only item-level explicit reuse rights."""
    from core.config import NARA_API_KEY
    if not NARA_API_KEY:
        return []
    data = _get_json(
        "https://catalog.archives.gov/api/v2/records/search",
        {"q": query, "availableOnline": "true", "typeOfMaterials": "Moving Images", "rows": 10},
        {"x-api-key": NARA_API_KEY, "Content-Type": "application/json"},
    )
    hits = (((data or {}).get("body") or {}).get("hits") or {}).get("hits") or []
    results = []
    for hit in hits:
        record = (((hit.get("_source") or {}).get("record")) or {})
        restriction = (record.get("useRestriction") or {}).get("status", "")
        notes = " ".join(str(record.get(k) or "") for k in
                         ("copyrightNote", "scopeAndContentNote", "generalNote", "title"))
        # An unrestricted access flag is NOT a copyright licence. Require an
        # explicit public-domain / US-government-work statement in item metadata.
        if str(restriction).lower() not in {"unrestricted", "unrestricted use"}:
            continue
        if not re.search(r"public domain|work of the u\.s\. federal government|\bu\.s\. government work\b", notes, re.I):
            continue
        naid = record.get("naId") or hit.get("_id")
        page = f"https://catalog.archives.gov/id/{naid}" if naid else ""
        for obj in record.get("digitalObjects") or []:
            obj_url = obj.get("objectUrl")
            object_type = str(obj.get("objectType") or "").lower()
            filename = str(obj.get("objectFilename") or obj_url or "").lower()
            if not obj_url or not str(obj_url).startswith("https://"):
                continue
            if not ("video" in object_type or filename.endswith((".mp4", ".mov", ".m4v", ".mpeg", ".mpg"))):
                continue
            results.append((obj_url, {
                "provider": "National Archives (NARA)", "asset_id": str(naid),
                "title": record.get("title"), "page_url": page,
                "licence": "Public Domain", "licence_url": page,
                "rights_statement": notes[:3000], "access_restriction": (record.get("accessRestriction") or {}).get("status"),
                "use_restriction": restriction, "query": query,
                "date_accessed": date.today().isoformat(),
                "attribution_text": f"{record.get('title') or 'Untitled'} — U.S. National Archives, NAID {naid}; {page}",
                "licence_status": "Explicit public-domain statement in NARA item metadata; verify record notes and third-party content",
            }))
    return results


def _dvids(query):
    """Search DVIDS video records and use its published commercial-use API terms."""
    api_key = os.getenv("DVIDS_API_KEY", "").strip()
    if not api_key:
        return []
    data = _get_json("https://api.dvidshub.net/search", {
        "api_key": api_key, "q": query, "type": "video", "page": 1, "max_results": 10,
    }, {
        "Accept": "application/json, text/javascript",
        "Referer": "https://api.dvidshub.net",
        "User-Agent": USER_AGENT,
    })
    hits = (data or {}).get("results") or []
    results = []
    for hit in hits if isinstance(hits, list) else []:
        if not isinstance(hit, dict) or str(hit.get("type", "")).lower() != "video":
            continue
        asset_id = hit.get("id")
        page = hit.get("url")
        url = hit.get("hls_url") or hit.get("download_url") or hit.get("downloadUrl")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        if not asset_id or not page or not str(page).startswith("https://www.dvidshub.net/"):
            continue
        title = hit.get("title") or "Untitled DVIDS video"
        credit = hit.get("credit") or ""
        rights = "DVIDS API Terms of Service: all media assets accessible via the API are free for commercial use."
        results.append((url, {
            "provider": "DVIDS", "asset_id": str(asset_id),
            "title": title, "creator": credit, "page_url": page,
            "licence": "DVIDS API Commercial Use Terms",
            "licence_url": "https://api.dvidshub.net/docs/tos",
            "rights_statement": rights, "query": query,
            "date_accessed": date.today().isoformat(),
            "attribution_text": f"{title} — {credit or 'DVIDS'}; {page}",
            "licence_status": "DVIDS API terms allow commercial use; inspect the exact item page, credit and third-party material before publication",
            "human_visual_inspection_required": True,
        }))
    return results


def _europeana_cc(query):
    """Discover Europeana video objects with an explicit CC0/CC BY rights URI."""
    from core.config import EUROPEANA_API_KEY
    if not EUROPEANA_API_KEY:
        return []
    data = _get_json("https://api.europeana.eu/record/v2/search.json", {
        "wskey": EUROPEANA_API_KEY, "query": query, "qf": "TYPE:VIDEO",
        "media": "true", "rows": 10,
        "profile": "standard",
    })
    results = []
    for item in (data or {}).get("items", []):
        rights_values = item.get("rights") or []
        rights_url = next((str(x) for x in rights_values if isinstance(x, str)), "")
        low = rights_url.lower()
        if "creativecommons.org/publicdomain/zero" in low:
            licence = "CC0 1.0"
        elif re.search(r"creativecommons\.org/licenses/by/(?:[0-9.]+)", low):
            licence = "CC BY 4.0"
        elif re.search(r"creativecommons\.org/licenses/by-sa/(?:[0-9.]+)", low):
            licence = "CC BY-SA 4.0"
        else:
            continue
        objects = item.get("edmIsShownBy") or item.get("edmObject") or []
        if isinstance(objects, str):
            objects = [objects]
        url = next((str(x) for x in objects if isinstance(x, str) and
                    re.search(r"\.(mp4|mov|webm|m4v)(?:\?|$)", x, re.I)), None)
        if not url or not url.startswith("https://"):
            continue
        page = item.get("guid") or item.get("edmIsShownAt") or ""
        title_values = item.get("title") or []
        creator_values = item.get("dcCreator") or []
        title = title_values[0] if title_values else "Untitled Europeana video"
        creator = creator_values[0] if creator_values else "Creator not supplied"
        results.append((url, {
            "provider": "Europeana CC", "asset_id": str(item.get("id") or page),
            "title": title, "creator": creator, "page_url": page,
            "licence": licence, "licence_url": rights_url, "query": query,
            "date_accessed": date.today().isoformat(),
            "attribution_text": f"{title} — {creator}; {licence}; {page}",
            "licence_status": "Explicit item-level CC rights URI; verify source institution terms and any embedded third-party rights",
        }))
    return results


def register_mixkit_asset(source_file, *, title, source_url, licence, creator="", attribution=""):
    """Register a user-selected Mixkit Free License download; never scrapes Mixkit."""
    from shutil import copy2
    from datetime import date as _date
    source = Path(source_file)
    if not source.is_file():
        raise FileNotFoundError(source)
    if str(licence).strip().lower() != "mixkit free license":
        raise ValueError("Only an explicitly verified 'Mixkit Free License' asset can be registered; Restricted License is not accepted.")
    if not str(source_url).startswith("https://mixkit.co/"):
        raise ValueError("source_url must be the exact Mixkit asset page URL.")
    if not title.strip():
        raise ValueError("The exact Mixkit clip title is required.")
    target_dir = Path("assets/clips/mixkit").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source.resolve() != target.resolve():
        copy2(source, target)
    metadata = {
        "provider": "Mixkit (manual user-selected import)",
        "asset_id": source.stem,
        "title": title.strip(),
        "creator": creator.strip(),
        "page_url": source_url,
        "licence": "Mixkit Free License",
        "licence_url": "https://mixkit.co/license/",
        "attribution_text": attribution.strip() or f"{title.strip()} — Mixkit; {source_url}",
        "date_accessed": _date.today().isoformat(),
        "terms_reviewed": True,
        "review_basis": "User confirmed this exact asset is labelled Free License on its Mixkit page.",
        "automated_download": False,
        "human_visual_inspection_required": True,
        "visual_checks": {"steady_clean": "not_automatically_verified",
                          "watermark_logo": "not_automatically_verified",
                          "private_person_privacy": "not_automatically_verified"},
    }
    sidecar = target.with_suffix(target.suffix + ".source.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, sidecar


def _tokens(value):
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "about",            "scene", "footage", "video", "documentary", "file"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) > 2 and word not in stop}


def _metadata_relevance(query, metadata):
    wanted = _tokens(query)
    found = (_tokens(metadata.get("title")) | _tokens(metadata.get("description"))
             | _tokens(metadata.get("tags")) | _tokens(metadata.get("creator")))
    return len(wanted & found) / len(wanted) if wanted else 0.0


def _is_archival_video_request(keywords=None, scene_prompt=""):
    """Identify requests that require genuine historical/archival moving images."""
    text = " ".join(str(value or "") for value in (keywords or [])) + " " + str(scene_prompt or "")
    return bool(re.search(
        r"\b(archival\s+(?:footage|film|video|newsreel)|archive\s+footage|newsreel|historical\s+(?:footage|film|newsreel)|period\s+(?:film|footage)|old\s+footage)\b",
        text, re.I
    ))


def _expanded_queries(keywords, scene_prompt=""):
    values = []
    for item in keywords or []:
        query = str(item).strip()
        if query and query not in values:
            values.append(query)
    context = " ".join(values) + " " + str(scene_prompt or "")
    context_tokens = _tokens(context)
    india_terms = {"india", "indian", "mughal", "maratha", "british", "delhi",
                   "mumbai", "bombay", "kolkata", "calcutta", "plassey",
                   "panipat", "1857", "independence", "east", "company"}
    if context_tokens & india_terms:
        for query in list(values):
            specific = f"{query} India"
            if specific not in values:
                values.append(specific)
    if _is_archival_video_request(keywords, scene_prompt):
        base = next((item for item in values if item), str(scene_prompt or "").strip())
        additions = []
        if re.search(r"chernobyl", base, re.I):
            additions = ["Chernobyl 1986 disaster film",
                         "Chernobyl nuclear accident 1986 evacuation",
                         "Pripyat 1986 Soviet newsreel",
                         "Chernobyl nuclear power plant 1986"]
        elif re.search(r"apollo|moon landing|saturn v", base, re.I):
            additions = ["Apollo 11 1969 NASA launch film",
                         "Saturn V Apollo 11 launch 1969",
                         "Apollo 11 lunar landing 1969 footage",
                         "Apollo 11 mission control 1969"]
        else:
            additions = [f"{base} historical film", f"{base} newsreel"]
        for query in additions:
            if query not in values:
                values.append(query)
    return values[:12]

def fetch_documentary_clip(keywords, path, used_ids=None, scene_prompt=""):
    """Search free archives with conservative metadata relevance ranking.

    Text matching is a discovery filter, not computer-vision verification.
    """
    used_ids = used_ids if used_ids is not None else set()
    context = " ".join(str(x) for x in (keywords or [])) + " " + str(scene_prompt or "")
    archival_request = _is_archival_video_request(keywords, scene_prompt)
    is_space = bool(_tokens(context) & {"space", "nasa", "planet", "mars", "moon", "astronaut", "rocket"})
    providers = ((_nasa, _nara, _dvids, _loc, _wikimedia, _internet_archive, _europeana_cc) if is_space
                 else (_nara, _dvids, _loc, _wikimedia, _internet_archive, _europeana_cc))
    # Prefer operator-imported Indian archive and Mixkit clips when sidecars prove an allowed licence.
    for folder in (Path("assets/clips/archival_trial"), Path("assets/clips/indian_archives"), Path("assets/clips/mixkit")):
        if not folder.is_dir():
            continue
        for media_path in sorted(folder.iterdir()):
            if not media_path.is_file() or media_path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
                continue
            sidecar = media_path.with_suffix(media_path.suffix + ".source.json")
            if not sidecar.is_file():
                continue
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            from core.asset_licensing import licence_is_usable
            usable, _reason = licence_is_usable(metadata)
            if not usable:
                continue
            identity = f"{metadata.get('provider')}:{metadata.get('asset_id') or media_path.name}"
            if identity in used_ids:
                continue
            local_query = " ".join(str(value) for value in (keywords or []) if str(value).strip())
            if not local_query:
                local_query = str(scene_prompt or "")
            score = _metadata_relevance(local_query, metadata)
            if score < 0.25 or not _valid_video(media_path):
                continue
            import shutil
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(media_path, target)
            target.with_suffix(target.suffix + ".source.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            used_ids.add(identity)
            print(f"  clip ok (local licensed import: {metadata.get('provider')}); human review required")
            return True

    # Search every expanded phrase before ranking/downloading. Previously this
    # loop stopped after three phrases, so later topic-specific variants were
    # silently never queried. Collect and deduplicate results across providers
    # first, then apply the same relevance and rights gates to the best leads.
    discovered = {}
    for query in _expanded_queries(keywords, scene_prompt):
        for provider in providers:
            try:
                provider_results = provider(query) or []
            except Exception as exc:
                # Isolate catalogue failures; continue searching other archives.
                print(f"  archival provider skipped ({getattr(provider, '__name__', 'unknown')}): {type(exc).__name__}: {exc}")
                continue
            for url, metadata in provider_results:
                if not isinstance(url, str) or not isinstance(metadata, dict):
                    continue
                score = _metadata_relevance(query, metadata)
                identity = f"{metadata.get('provider')}:{metadata.get('asset_id') or url}"
                previous = discovered.get(identity)
                if previous is None or score > previous[0]:
                    discovered[identity] = (score, query, url, metadata)

    ranked = sorted(discovered.values(), key=lambda item: item[0], reverse=True)
    # Bound downloads even when a broad catalogue returns many duplicates or
    # weak leads. Search coverage is not truncated by this download safety cap.
    for score, query, url, metadata in ranked[:20]:
        metadata["relevance_score"] = round(score, 3)
        metadata["scene_prompt"] = str(scene_prompt)[:500]
        metadata["date_accessed"] = metadata.get("date_accessed") or date.today().isoformat()
        if archival_request:
            # This is an archive candidate, not an automatic authenticity
            # certification. Capture the fields a reviewer must verify.
            metadata["asset_type"] = "archival_video_candidate"
            metadata["archival_material"] = True
            metadata["archival_authenticity_status"] = "pending_human_verification"
            metadata["archival_provenance_fields"] = {
                "item_page": bool(metadata.get("page_url")),
                "rights_statement": bool(metadata.get("licence") and metadata.get("licence_url")),
                "creator_or_holding_institution": bool(metadata.get("creator") or metadata.get("provider")),
                "date_or_period": bool(metadata.get("date_created") or metadata.get("date") or metadata.get("year") or metadata.get("description")),
            }
            metadata["archival_provenance_complete"] = all(metadata["archival_provenance_fields"].values())
            metadata["human_visual_inspection_required"] = True
            metadata["human_context_and_authenticity_review_required"] = True
        licence = str(metadata.get("licence") or "").lower()
        licence_url = str(metadata.get("licence_url") or "")
        known = bool(licence and licence_url and (
            "public domain" in licence or licence.startswith("cc0")
            or licence.startswith("cc by") or licence.startswith("cc by-sa")
        ))
        if not known or score < 0.25:
            continue
        if discovered and f"{metadata.get('provider')}:{metadata.get('asset_id') or url}" in used_ids:
            continue
        if _download(url, path, metadata):
            used_ids.add(f"{metadata.get('provider')}:{metadata.get('asset_id') or url}")
            return True
    return False

def register_indian_archive_asset(
    source_file, *, provider, title, source_url, licence_reference,
    usage_scope, creator="", attribution="", licence_expiry=""
):
    """Register a manually obtained Indian archive clip with documented YouTube rights.

    NCAA and Prasar Bharati do not expose a documented public bulk-download API
    for this workflow. This deliberately avoids scraping/downloading their sites.
    The operator must obtain the exact clip and written permission/licence first.
    """
    from shutil import copy2
    from datetime import date as _date
    from urllib.parse import urlparse

    provider = str(provider or "").strip()
    allowed = {
        "NCAA": ("ncaa.gov.in", "NCAA written permission"),
        "Prasar Bharati Archives": ("prasarbharati.gov.in", "Prasar Bharati licensed footage"),
    }
    if provider not in allowed:
        raise ValueError("provider must be NCAA or Prasar Bharati Archives.")
    host = (urlparse(str(source_url)).hostname or "").lower()
    allowed_host, licence_label = allowed[provider]
    if not (host == allowed_host or host.endswith("." + allowed_host)):
        raise ValueError("source_url must be the official provider item page.")
    if not str(source_url).startswith("https://"):
        raise ValueError("source_url must use HTTPS.")
    if not str(title).strip():
        raise ValueError("The exact archive item title is required.")
    if not str(licence_reference).strip():
        raise ValueError("A written permission or licence reference is required.")
    scope = str(usage_scope or "").strip()
    scope_low = scope.lower()
    if "youtube" not in scope_low or not any(x in scope_low for x in ("commercial", "monetized", "monetised")):
        raise ValueError("The written permission must explicitly cover commercial/monetized YouTube use.")
    source = Path(source_file)
    if not source.is_file():
        raise FileNotFoundError(source)
    target_dir = Path("assets/clips/indian_archives").resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source.resolve() != target.resolve():
        copy2(source, target)
    metadata = {
        "provider": provider,
        "asset_id": source.stem,
        "title": str(title).strip(),
        "creator": str(creator).strip(),
        "page_url": str(source_url).strip(),
        "licence": licence_label,
        "licence_url": str(source_url).strip(),
        "licence_document_ref": str(licence_reference).strip(),
        "usage_scope": scope,
        "licence_expiry": str(licence_expiry).strip(),
        "attribution_text": str(attribution).strip() or f"{str(title).strip()} — {provider}; {source_url}",
        "date_accessed": _date.today().isoformat(),
        "rights_confirmed_by_operator": True,
        "review_basis": "Operator confirmed written permission/licence for this exact item and commercial YouTube use.",
        "automated_download": False,
        "human_visual_inspection_required": True,
    }
    sidecar = target.with_suffix(target.suffix + ".source.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, sidecar