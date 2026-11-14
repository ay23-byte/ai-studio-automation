import os
import time, random, base64, urllib.parse, subprocess, hashlib
from pathlib import Path
import requests
from core.config import CF_ACCOUNT_ID, CF_API_TOKEN, POLLINATIONS_GAP, PIXABAY_API_KEY, COVERR_API_KEY

# Authorization failures are stable for the lifetime of one production run.
_DISABLED_VIDEO_PROVIDERS = set()

def _tokens(value):
    import re
    stop = {"the","and","for","with","from","that","this","into","over","under","about","scene","shot","video","image","documentary","historical","history"}
    return {w for w in re.findall(r"[a-z0-9]+", str(value or "").lower()) if len(w) > 2 and w not in stop}

def _compact_search_query(value, limit=100):
    """Keep provider search terms concise and within documented API limits."""
    import re
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    words = text.split()
    result = ""
    for word in words:
        candidate = (result + " " + word).strip()
        if len(candidate) > limit:
            break
        result = candidate
    return result or text[:limit].rstrip()


def _relevance(query, title="", tags=""):
    wanted = _tokens(query)
    found = _tokens(title) | _tokens(tags)
    return len(wanted & found) / len(wanted) if wanted else 0.0

def _image_style(category="general"):
    styles = {
        "history": "Historically grounded cinematic documentary reconstruction, accurate period clothing, architecture, tools and geography, natural film grain, realistic lighting; illustrative reconstruction, not archival. ",
        "geopolitics": "Photorealistic current-affairs documentary B-roll, geographically accurate environments, realistic materials and restrained cinematic lighting. ",
        "science": "Scientifically accurate laboratory visualization, correct instruments and physical processes, clean documentary lighting. ",
        "physics": "Scientifically accurate physics visualization, realistic laboratory apparatus and physical models. ",
        "technology": "Photorealistic technology documentary B-roll showing the exact device or process and credible components. ",
        "finance": "Premium cinematic financial education visual, dark navy with restrained gold and teal accents. ",
    }
    return styles.get(str(category or "general").lower(), "Photorealistic documentary B-roll depicting the exact described subject and setting, natural cinematic lighting. ")

def _ok(p):
    try:
        if p.stat().st_size < 10_000: return False
        from core.ffmpeg_utils import ffprobe_bin
        out = subprocess.check_output([ffprobe_bin(),"-v","error","-select_streams","v:0",
            "-show_entries","stream=width,height","-of","csv=p=0",str(p)],
            stderr=subprocess.DEVNULL, timeout=60).decode().strip().split(",")
        return int(out[0]) >= 200 and int(out[1]) >= 200
    except Exception:  # never swallow KeyboardInterrupt/SystemExit
        return False

def _write_source_metadata(path, metadata):
    import json
    sidecar = path.with_suffix(path.suffix + ".source.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _cloudflare(prompt, path):
    # Permanently disabled: this hosted inference endpoint can incur charges.
    print("  Cloudflare AI image generation disabled by the zero-paid project policy.")
    return False
    url = (f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
           f"/ai/run/@cf/black-forest-labs/flux-1-schnell")
    for attempt in range(2):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
                json={"prompt": prompt[:2000], "steps": 4}, timeout=120)
            if r.status_code == 429: time.sleep(20*(attempt+1)); continue
            r.raise_for_status()
            ct = r.headers.get("content-type","")
            data = base64.b64decode((r.json().get("result") or {}).get("image","")) if "json" in ct else r.content
            if not data: return False
            path.write_bytes(data)
            if not _ok(path):
                return False
            _write_source_metadata(path, {
                "provider": "Cloudflare Workers AI",
                "model": "@cf/black-forest-labs/flux-1-schnell",
                "asset_type": "ai_generated_image",
                "page_url": "https://developers.cloudflare.com/workers-ai/",
                "licence_url": "https://www.cloudflare.com/terms/",
                "terms_reviewed": False,
                "ai_disclosure": True,
                "licence_status": "Check current Cloudflare AI terms and any applicable model/provider restrictions before commercial publication.",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            })
            return True
        except Exception as e: print(f"Cloudflare {attempt+1}: {e}")
    return False

# Consecutive network failures of the free image endpoint in this process.
_POLLINATIONS_STATE = {"failures": 0}


def _image_dimensions(prompt):
    """Request the frame the scene needs instead of always a 9:16 portrait.

    Root cause: every image (including 16:9 documentary shots and thumbnails)
    was requested as 768x1344 and then centre-cropped to landscape, discarding
    most of the picture and upscaling the remainder.
    """
    text = str(prompt or "").lower()
    if "9:16" in text or "vertical" in text or "portrait" in text:
        return 768, 1344
    if "16:9" in text or "landscape" in text or "widescreen" in text:
        return 1344, 768
    return 768, 1344


def _pollinations(prompt, path):
    limit = max(1, int(os.getenv("IMAGE_PROVIDER_FAILURE_LIMIT", "2") or 2))
    if _POLLINATIONS_STATE["failures"] >= limit:
        # The endpoint was unreachable repeatedly: do not wait out the polite
        # gap and a long timeout again for every remaining image of this run.
        return False
    time.sleep(POLLINATIONS_GAP)
    width, height = _image_dimensions(prompt)
    try:
        r = requests.get("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900]),
            params={"width":width,"height":height,"model":"flux","nologo":"true",
                    "seed":random.randint(1,10**6)}, timeout=150)
        r.raise_for_status()
        _POLLINATIONS_STATE["failures"] = 0
        if "image" not in r.headers.get("content-type","image"): return False
        path.write_bytes(r.content)
        if not _ok(path):
            return False
        _write_source_metadata(path, {
            "provider": "Pollinations",
            "model": "flux",
            "asset_type": "ai_generated_image",
            "page_url": "https://pollinations.ai/",
            "licence_url": "https://pollinations.ai/terms",
            "terms_reviewed": False,
            "ai_disclosure": True,
            "licence_status": "Check current Pollinations service/model terms and any applicable restrictions before commercial publication.",
            "prompt_sha256": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest(),
        })
        return True
    except (requests.ConnectionError, requests.Timeout) as e:
        _POLLINATIONS_STATE["failures"] += 1
        reason = str(e).split("(Caused by")[-1].strip(" )")[:160]
        print(f"Pollinations unreachable ({_POLLINATIONS_STATE['failures']}/{limit}): {type(e).__name__}: {reason}")
        if _POLLINATIONS_STATE["failures"] >= limit:
            print("  image provider unreachable; skipping it for the rest of this run.")
        return False
    except Exception as e:
        print(f"Pollinations: {type(e).__name__}: {str(e)[:200]}"); return False

def generate_image(scene_prompt, path, category="general"):
    # A scene explicitly requesting real archival material must never silently
    # fall through to a synthetic image generator.
    import re
    archival_request = bool(re.search(
        r"\b(archival\s+(?:footage|film|video|photograph|photo|still|image)|archive\s+footage|newsreel|period\s+(?:photograph|photo|film)|historical\s+(?:footage|film|photograph)|old\s+footage)\b",
        str(scene_prompt or ""), re.I
    ))
    if archival_request:
        from core.archival_images import fetch_archival_image
        if fetch_archival_image(scene_prompt, path):
            print("  genuine archival still candidate retrieved; human review required")
            return True
        print("  archival still unavailable; synthetic-image substitution refused")
        return False

    full = _image_style(category) + scene_prompt
    if _cloudflare(full, path): print(f"  image ok (cloudflare)"); return True
    if _pollinations(full, path): print(f"  image ok (pollinations)"); return True
    print(f"  image failed: {scene_prompt[:50]}"); return False

COVERR_LICENCE_URL = "https://coverr.co/license"
PIXABAY_LICENCE_URL = "https://pixabay.com/service/license-summary/"
# One answer per query per run: scenes repeat queries, and both libraries meter requests.
_STOCK_SEARCH_CACHE = {}
_STOCK_DISABLED_REASON = {}


def _stock_event(provider, *, status=None, failure=None, search_status=None, retry=False, disabled=None):
    """Record a stock-library HTTP outcome in the run's provider report."""
    if disabled:
        _STOCK_DISABLED_REASON[str(provider).lower()] = str(disabled)
    try:
        from core.footage_report import active_scope, current_report
        _name, report = active_scope()
        report = report or current_report()
        if status is not None:
            report.http_status(provider, status)
        if retry:
            report.add(provider, "retries")
        if failure:
            report.fail(provider, failure, search_status=search_status)
        if disabled:
            report.set(provider, disabled_reason=disabled)
    except Exception:
        pass


def _wait_seconds(response, default):
    """Seconds a rate-limited provider asked us to wait (Retry-After or X-RateLimit-Reset)."""
    headers = getattr(response, "headers", None) or {}
    for name in ("Retry-After", "X-RateLimit-Reset"):
        try:
            value = headers.get(name)
            if value is not None and str(value).strip() != "":
                return max(0.0, float(value))
        except (TypeError, ValueError, AttributeError):
            continue
    return float(default)


_STOCK_NETWORK_FAILURES = {}
STOCK_NETWORK_FAILURE_LIMIT = 2


def _stock_unreachable(provider, exc):
    """Count a search that failed on every attempt; switch the provider off after two in a row.

    Without this an unreachable provider costs three timeouts for every query of every scene.
    """
    key = provider.lower()
    _STOCK_NETWORK_FAILURES[key] = _STOCK_NETWORK_FAILURES.get(key, 0) + 1
    if _STOCK_NETWORK_FAILURES[key] >= STOCK_NETWORK_FAILURE_LIMIT:
        reason = f"unreachable after repeated attempts ({type(exc).__name__}); skipped for the rest of this run"
        _DISABLED_VIDEO_PROVIDERS.add(key)
        _stock_event(provider, disabled=reason)
        print(f"  {provider} unreachable; skipping it for the rest of this run.")


def reset_stock_state():
    """Forget stock-provider circuit breakers and cached searches (new run or test)."""
    _DISABLED_VIDEO_PROVIDERS.clear()
    _STOCK_NETWORK_FAILURES.clear()
    _STOCK_SEARCH_CACHE.clear()
    _STOCK_DISABLED_REASON.clear()


def _max_rate_limit_wait():
    try:
        return float(os.getenv("FOOTAGE_MAX_RETRY_AFTER_SECONDS", "") or 30)
    except ValueError:
        return 30.0


def _search_coverr(q):
    """Search Coverr's official API. Returns raw hits; no scraping is used.

    Coverr documents two ways to send the key: ``Authorization: Bearer <key>`` or
    an ``api_key`` query parameter. The header keeps the key out of URLs and logs.
    """
    if not COVERR_API_KEY or "coverr" in _DISABLED_VIDEO_PROVIDERS:
        return []
    key = ("coverr", q)
    if key in _STOCK_SEARCH_CACHE:
        return _STOCK_SEARCH_CACHE[key]
    for attempt in range(3):
        try:
            response = requests.get(
                "https://api.coverr.co/videos",
                headers={"Authorization": f"Bearer {COVERR_API_KEY}", "Accept": "application/json"},
                params={"query": q, "page_size": 20, "urls": "true"},
                timeout=(10, 30),
            )
            status = response.status_code
            _stock_event("Coverr", status=status)
            if status == 429:
                wait = _wait_seconds(response, 5 * (attempt + 1))
                if wait > _max_rate_limit_wait() or attempt == 2:
                    reason = (f"HTTP 429 (rate limited); Coverr asked for a {wait:.0f}s wait. The free API tier "
                              "allows about 50 requests an hour, so Coverr is skipped for the rest of this run")
                    _DISABLED_VIDEO_PROVIDERS.add("coverr")
                    _stock_event("Coverr", failure=reason, search_status="rate_limited", disabled=reason)
                    print(f"  Coverr paused for this run: {reason}.")
                    return []
                _stock_event("Coverr", retry=True)
                time.sleep(wait + random.uniform(0, 1))
                continue
            if status in (401, 403):
                reason = (f"HTTP {status}: Coverr did not accept the key. Check COVERR_API_KEY (sent as "
                          "'Authorization: Bearer'); a demo key must be approved by Coverr before it works")
                _DISABLED_VIDEO_PROVIDERS.add("coverr")
                _stock_event("Coverr", failure=reason, search_status="auth_rejected", disabled=reason)
                print(f"  Coverr disabled for this run: HTTP {status}")
                return []
            if status in (400, 404):
                _stock_event("Coverr", failure=f"HTTP {status} for this query", search_status="http_error")
                print(f"  Coverr query unsupported (HTTP {status}); skipping retries")
                return []
            response.raise_for_status()
            payload = response.json()
            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            hits = hits if isinstance(hits, list) else []
            _STOCK_SEARCH_CACHE[key] = hits
            _STOCK_NETWORK_FAILURES.pop("coverr", None)
            return hits
        except Exception as exc:
            from core.footage_report import redact
            _stock_event("Coverr", failure=f"{type(exc).__name__}: {redact(exc)}"[:300], search_status="network_error")
            print(f"  Coverr search attempt {attempt + 1} failed: {type(exc).__name__}: {redact(exc)}")
            if attempt < 2:
                time.sleep(1 + attempt + random.uniform(0, 1))
            else:
                _stock_unreachable("Coverr", exc)
    return []


def _coverr_candidate(hit, query):
    """Convert a Coverr API hit into a downloadable candidate and licence record."""
    from datetime import date

    if not isinstance(hit, dict):
        return None
    urls = hit.get("urls") if isinstance(hit.get("urls"), dict) else {}
    video_url = (
        hit.get("mp4_download")
        or hit.get("mp4")
        or urls.get("mp4_download")
        or urls.get("mp4")
    )
    if isinstance(video_url, dict):
        video_url = video_url.get("url") or video_url.get("src")
    if not isinstance(video_url, str) or not video_url.startswith("https://"):
        return None

    title = str(hit.get("title") or hit.get("name") or "")
    description = str(hit.get("description") or "")
    tags = hit.get("tags") or []
    if isinstance(tags, list):
        tags_text = " ".join(str(tag.get("name") if isinstance(tag, dict) else tag) for tag in tags)
    else:
        tags_text = str(tags)
    relevance = max(
        _relevance(query, title, tags_text),
        _relevance(query, description, tags_text),
    )
    width = int(hit.get("max_width") or hit.get("width") or hit.get("video_width") or 0)
    height = int(hit.get("max_height") or hit.get("height") or hit.get("video_height") or 0)
    asset_id = hit.get("id") or hit.get("video_id") or hit.get("slug")
    if not asset_id:
        return None

    page_url = str(hit.get("url") or hit.get("page_url") or "")
    if page_url and page_url.startswith("/"):
        page_url = "https://coverr.co" + page_url
    page_derived = False
    if not page_url:
        page_url = f"https://coverr.co/videos/{hit.get('slug') or asset_id}"
        page_derived = True
    creator = hit.get("creator") or hit.get("author") or hit.get("user") or ""
    if isinstance(creator, dict):
        creator = creator.get("name") or creator.get("username") or ""
    creator = str(creator or "").strip()
    try:
        duration = float(hit.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    metadata = {
        "provider": "Coverr",
        "asset_id": str(asset_id),
        "title": title,
        "description": description,
        "tags": tags_text,
        "creator": creator,
        "page_url": page_url,
        "query": query,
        "relevance_score": relevance,
        "declared_width": width,
        "declared_height": height,
        "declared_duration_seconds": duration,
        "licence": "Coverr License",
        "licence_url": COVERR_LICENCE_URL,
        # Coverr's free API tier requires a visible credit with a link.
        "attribution_text": (f"Video by {creator} on Coverr" if creator else f"{title or 'Video'}, Coverr") + f"; {page_url}",
        "date_accessed": date.today().isoformat(),
        "terms_reviewed": False,
        "human_visual_inspection_required": True,
        "visual_checks": {
            "steady_clean": "not_automatically_verified",
            "watermark_logo": "not_automatically_verified",
            "private_person_privacy": "not_automatically_verified",
        },
    }
    if page_derived:
        metadata["review_notes"] = "The Coverr API gave no page link; the page URL was built from the video id and must be opened by the reviewer."
    if hit.get("is_ai_generated"):
        # AI-generated stock is not real-world footage; the licence policy refuses it.
        metadata["ai_generated"] = True
    return video_url, metadata


def _coverr_download_ping(metadata):
    """Tell Coverr a video was downloaded, as its API terms require for real downloads."""
    asset_id = str((metadata or {}).get("asset_id") or "").strip()
    if not asset_id or not COVERR_API_KEY:
        return
    try:
        response = requests.patch(
            f"https://api.coverr.co/videos/{urllib.parse.quote(asset_id)}/stats/downloads",
            headers={"Authorization": f"Bearer {COVERR_API_KEY}"}, timeout=(10, 20))
        metadata["coverr_download_reported"] = response.status_code < 400
    except Exception as exc:
        metadata["coverr_download_reported"] = False
        print(f"  Coverr download report failed: {type(exc).__name__}")


def _search_pixabay(q):
    # Pixabay's q parameter is limited to 100 characters. Enforce this here
    # as well as at query construction so future callers cannot break builds.
    q = _compact_search_query(q, 100)
    if not q:
        return []
    if not PIXABAY_API_KEY:
        print("  Pixabay skipped: PIXABAY_API_KEY is not configured.")
        return []
    if "pixabay" in _DISABLED_VIDEO_PROVIDERS:
        return []
    key = ("pixabay", q)
    if key in _STOCK_SEARCH_CACHE:
        return _STOCK_SEARCH_CACHE[key]
    for attempt in range(3):
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                params={"key":PIXABAY_API_KEY,"q":q,"per_page":30,
                        "safesearch":"true","video_type":"film"}, timeout=(10, 30))
            _stock_event("Pixabay", status=r.status_code)
            if r.status_code == 429:
                # Pixabay allows 100 requests per 60 seconds and reports the reset time.
                wait = _wait_seconds(r, 10 * (attempt + 1))
                if wait > _max_rate_limit_wait() or attempt == 2:
                    reason = (f"HTTP 429 (rate limited); Pixabay asked for a {wait:.0f}s wait, "
                              "so it is skipped for the rest of this run")
                    _DISABLED_VIDEO_PROVIDERS.add("pixabay")
                    _stock_event("Pixabay", failure=reason, search_status="rate_limited", disabled=reason)
                    print(f"  Pixabay paused for this run: {reason}.")
                    return []
                print(f"  Pixabay rate limited (HTTP 429); retrying after {wait:.0f}s.")
                _stock_event("Pixabay", retry=True)
                time.sleep(wait + random.uniform(0, 1))
                continue
            if r.status_code in (401, 403):
                reason = f"HTTP {r.status_code}: Pixabay did not accept the key; check PIXABAY_API_KEY"
                _DISABLED_VIDEO_PROVIDERS.add("pixabay")
                _stock_event("Pixabay", failure=reason, search_status="auth_rejected", disabled=reason)
                print(f"  Pixabay disabled for this run: HTTP {r.status_code}; check the repository secret.")
                return []
            if r.status_code != 200:
                # Pixabay returns short plain-text error messages for invalid keys,
                # unsupported parameters and exhausted quotas. Keep the diagnostic
                # short and never print the request URL (which contains the API key).
                try:
                    error_payload = r.json()
                    detail = error_payload.get("message") or error_payload.get("error") or str(error_payload)
                except (ValueError, AttributeError):
                    detail = " ".join((r.text or "")[:240].split())
                invalid_key = "api key" in str(detail).lower()
                if invalid_key:
                    _DISABLED_VIDEO_PROVIDERS.add("pixabay")
                _stock_event("Pixabay", failure=f"HTTP {r.status_code}: {str(detail)[:200]}",
                             search_status="auth_rejected" if invalid_key else "http_error")
                print(f"  Pixabay search failed: HTTP {r.status_code}: {str(detail)[:300]}; query skipped.")
                return []
            payload = r.json()
            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            if not isinstance(hits, list):
                _stock_event("Pixabay", failure="unexpected response shape", search_status="invalid_response")
                print("  Pixabay returned an unexpected response shape.")
                return []
            _STOCK_SEARCH_CACHE[key] = hits
            _STOCK_NETWORK_FAILURES.pop("pixabay", None)
            return hits
        except Exception as exc:
            from core.footage_report import redact
            _stock_event("Pixabay", failure=f"{type(exc).__name__}: {redact(exc)}"[:300], search_status="network_error")
            print(f"  Pixabay search attempt {attempt + 1} failed: {type(exc).__name__}: {redact(exc)}")
            if attempt < 2:
                time.sleep(1 + attempt + random.uniform(0, 1))
            else:
                _stock_unreachable("Pixabay", exc)
    return []


def _pixabay_candidate(hit, query, scene_prompt=""):
    """Convert a Pixabay API hit into a downloadable candidate and licence record."""
    from datetime import date
    if not isinstance(hit, dict) or not hit.get("id"):
        return None
    try:
        duration = float(hit.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration < 3:
        return None
    tags = str(hit.get("tags") or "")
    # Largest rendition up to full HD first; 4K files are slow to fetch and unnecessary.
    renditions = []
    for name, variant in (hit.get("videos") or {}).items():
        if not isinstance(variant, dict) or not str(variant.get("url") or "").startswith("https://"):
            continue
        width, height = int(variant.get("width") or 0), int(variant.get("height") or 0)
        if width < 720 or height < 400:
            continue
        renditions.append((max(width, height) > 1920, -(width * height), variant["url"], width, height,
                           int(variant.get("size") or 0), name))
    if not renditions:
        return None
    renditions.sort()
    _over, _area, url, width, height, size, name = renditions[0]
    user = str(hit.get("user") or "")
    page_url = str(hit.get("pageURL") or "")
    metadata = {
        "provider": "Pixabay", "asset_id": hit.get("id"),
        "title": tags, "tags": tags, "creator": user,
        "page_url": page_url, "query": query,
        "relevance_score": max(_relevance(query, tags), _relevance(scene_prompt, tags)),
        "declared_width": width, "declared_height": height, "declared_size_bytes": size,
        "declared_duration_seconds": duration, "rendition": name,
        "alternate_urls": [item[2] for item in renditions[1:3]],
        "licence": "Pixabay Content License",
        "licence_url": PIXABAY_LICENCE_URL,
        "attribution_text": f"{tags} by {user or 'Pixabay contributor'} — Pixabay Content License; {page_url}",
        "date_accessed": date.today().isoformat(),
        "human_visual_inspection_required": True,
        "visual_checks": {"steady_clean": "not_automatically_verified",
                          "watermark_logo": "not_automatically_verified",
                          "private_person_privacy": "not_automatically_verified"},
    }
    return url, metadata


def stock_providers(scene_prompt=""):
    """Coverr and Pixabay behind the common provider interface (see ``core.footage_engine``)."""
    from core.footage_engine import Provider

    def coverr_search(query):
        query = _compact_search_query(query, 100)
        hits = _search_coverr(query) if query else []
        _stock_count("Coverr", len(hits))
        return [item for item in (_coverr_candidate(hit, query) for hit in hits) if item]

    def pixabay_search(query):
        query = _compact_search_query(query, 100)
        hits = _search_pixabay(query) if query else []
        _stock_count("Pixabay", len(hits))
        return [item for item in (_pixabay_candidate(hit, query, scene_prompt) for hit in hits) if item]

    def status(name, key, env_name):
        def check():
            if not key:
                return False, "missing_key", f"{env_name} is not set"
            if name in _DISABLED_VIDEO_PROVIDERS:
                return False, "configured", _STOCK_DISABLED_REASON.get(
                    name, "switched off earlier in this run (see failure reason)")
            return True, "configured", ""
        return check

    return [
        Provider("Coverr", coverr_search, kind="stock", min_relevance=0.45,
                 status=status("coverr", COVERR_API_KEY, "COVERR_API_KEY"), after_download=_coverr_download_ping),
        Provider("Pixabay", pixabay_search, kind="stock", min_relevance=0.45,
                 status=status("pixabay", PIXABAY_API_KEY, "PIXABAY_API_KEY")),
    ]


def _stock_count(provider, amount):
    try:
        from core.footage_report import active_scope
        _name, report = active_scope()
        if report is not None:
            report.add(provider, "results_found", amount)
    except Exception:
        pass


def fetch_clip(keywords, path, used, scene_prompt=""):
    """Find one licensed, validated clip for a scene across every footage source.

    Archives (NASA, Wikimedia Commons, Internet Archive, Library of Congress,
    DVIDS, Europeana, NARA) and stock libraries (Coverr, Pixabay) are searched
    together and ranked as one pool. Mixkit is handled through explicit
    human-selected local assets because its published terms prohibit
    scripted/bot mass downloading and site scraping.
    """
    queries = []
    for value in list(keywords or []) + [scene_prompt]:
        query = _compact_search_query(value, 100)
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= 8:
            break

    path = Path(path)
    archival_request = False
    try:
        from core.footage_sources import fetch_documentary_clip, _is_archival_video_request
        archival_request = _is_archival_video_request(queries, scene_prompt)
        # A scene that asks for real archival moving images never receives
        # generic stock: the stock libraries are left out of its pool.
        extra = [] if archival_request else stock_providers(scene_prompt)
        if fetch_documentary_clip(queries, path, used, scene_prompt=scene_prompt, extra_providers=extra):
            return True
        if archival_request:
            print("  archival video unavailable; stock/AI-video substitution refused")
        else:
            print("  no clip met the relevance, licence and validation gates across configured sources")
        return False
    except Exception as exc:
        from core.footage_report import redact
        print(f"  footage search unavailable: {type(exc).__name__}: {redact(exc)}")
        import re
        if archival_request or re.search(r"\b(archival|archive footage|newsreel|historical footage|period film|historical film)\b",
                                         str(scene_prompt or ""), re.I):
            print("  archival video search failed; stock/AI-video substitution refused")
        return False


def _video_ok(path):
    """Accept only a real, non-trivial video file from the remote provider."""
    try:
        if path.stat().st_size < 50_000:
            return False
        out = subprocess.check_output(
            [ffprobe_bin(), "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)], stderr=subprocess.DEVNULL
        , timeout=PROBE_TIMEOUT).decode("utf-8")
        import json
        data = json.loads(out)
        duration = float((data.get("format") or {}).get("duration") or 0)
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        return bool(video and duration >= 2 and int(video.get("width") or 0) >= 256
                    and int(video.get("height") or 0) >= 256)
    except Exception:
        return False


_REPLICATE_VIDEO_CLIPS_CREATED = 0


def _replicate_output_url(output):
    """Extract a video URL from common Replicate output shapes."""
    if isinstance(output, str) and output.startswith("https://"):
        return output
    if isinstance(output, list):
        for item in output:
            found = _replicate_output_url(item)
            if found:
                return found
    if isinstance(output, dict):
        for key in ("video", "url", "output", "mp4"):
            found = _replicate_output_url(output.get(key))
            if found:
                return found
    return None


def _generate_replicate_video(prompt, path, aspect_ratio):
    """Generate one scene through an explicitly enabled, user-selected Replicate model."""
    import json
    global _REPLICATE_VIDEO_CLIPS_CREATED

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    model = os.getenv("REPLICATE_VIDEO_MODEL", "").strip()
    if not token or not model or "/" not in model:
        print("  Replicate video skipped: configure REPLICATE_API_TOKEN and REPLICATE_VIDEO_MODEL=owner/model.")
        return False

    maximum = max(0, int(os.getenv("AI_VIDEO_MAX_CLIPS", "3")))
    if _REPLICATE_VIDEO_CLIPS_CREATED >= maximum:
        print(f"  Replicate video clip limit reached ({maximum}); using other visual sources.")
        return False

    try:
        template = json.loads(os.getenv("REPLICATE_VIDEO_INPUT_JSON", '{"prompt":"{{prompt}}"}'))
    except ValueError:
        print("  Replicate video skipped: REPLICATE_VIDEO_INPUT_JSON is invalid JSON.")
        return False
    if not isinstance(template, dict):
        print("  Replicate video skipped: model input template must be a JSON object.")

        return False

    def substitute(value):
        if isinstance(value, str):
            return value.replace("{{prompt}}", str(prompt)).replace("{{aspect_ratio}}", aspect_ratio)
        if isinstance(value, list):
            return [substitute(item) for item in value]
        if isinstance(value, dict):
            return {key: substitute(item) for key, item in value.items()}
        return value

    model_input = substitute(template)
    if not any(isinstance(value, str) and str(prompt) in value for value in model_input.values()):
        model_input.setdefault("prompt", str(prompt))

    version = os.getenv("REPLICATE_VIDEO_VERSION", "").strip()
    if version:
        endpoint, payload = "https://api.replicate.com/v1/predictions", {"version": version, "input": model_input}
    else:
        endpoint = "https://api.replicate.com/v1/models/" + model + "/predictions"
        payload = {"input": model_input}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    timeout = max(60, int(os.getenv("AI_VIDEO_TIMEOUT_SECONDS", "480")))
    poll_interval = max(2, int(os.getenv("REPLICATE_POLL_INTERVAL_SECONDS", "5")))
    try:
        response = requests.post(endpoint, headers={**headers, "Prefer": "wait=60"}, json=payload, timeout=75)
        response.raise_for_status()
        prediction = response.json()
        _REPLICATE_VIDEO_CLIPS_CREATED += 1
        status = str(prediction.get("status") or "").lower()
        get_url = (prediction.get("urls") or {}).get("get")
        deadline = time.monotonic() + timeout
        while status not in {"succeeded", "successful", "failed", "canceled", "cancelled"}:
            if not get_url or time.monotonic() >= deadline:
                print("  Replicate video timed out or returned no polling URL.")
                return False
            time.sleep(poll_interval)
            poll = requests.get(get_url, headers=headers, timeout=30)
            poll.raise_for_status()
            prediction = poll.json()
            status = str(prediction.get("status") or "").lower()
        if status not in {"succeeded", "successful"}:
            print(f"  Replicate video prediction failed: {str(prediction.get('error') or status)[:300]}")
            return False
        output_url = _replicate_output_url(prediction.get("output"))
        if not output_url:
            print("  Replicate video completed but returned no downloadable video URL.")
            return False
        from core.downloads import fetch
        fetch(output_url, Path(path), kind="video", timeout=(20, 120))
        if not _video_ok(Path(path)):
            Path(path).unlink(missing_ok=True)
            print("  Replicate output failed FFprobe video validation.")
            return False
        _write_source_metadata(Path(path), {
            "provider": "Replicate", "model": model, "prediction_id": prediction.get("id"),
            "asset_type": "ai_generated_video", "ai_disclosure": True,
            "prompt_sha256": hashlib.sha256(str(prompt).encode("utf-8")).hexdigest(),
            "page_url": (prediction.get("urls") or {}).get("web", ""),
            "licence_status": "Review selected model licence and Replicate terms before publication.",
            "human_visual_inspection_required": True,
        })
        print(f"  Replicate AI video generated ({model}); human visual review required.")
        return True
    except Exception as exc:
        Path(path).unlink(missing_ok=True)
        print(f"  Replicate video generation failed: {type(exc).__name__}: {exc}")
        return False


def generate_ai_video(prompt, path, aspect_ratio="9:16"):
    """Generate AI video only through an explicitly enabled provider/model."""
    import re
    if re.search(
        r"\b(archival\s+(?:footage|film|video|photograph|photo|still|image)|archive\s+footage|newsreel|historical\s+(?:footage|film|photograph)|old\s+footage)\b",
        str(prompt or ""), re.I
    ):
        print("  AI video skipped: archival requests require genuine archival sources.")
        return False
    if os.getenv("FREE_ONLY_MODE", "0").strip().lower() in {"1", "true", "yes"}:
        print("  AI video blocked by FREE_ONLY_MODE; explicitly disable free-only mode to authorize paid generation.")
        return False
    if os.getenv("AI_VIDEO_ENABLED", "0").strip().lower() not in {"1", "true", "yes"}:
        print("  AI video disabled (AI_VIDEO_ENABLED is not explicitly enabled).")
        return False
    provider = os.getenv("AI_VIDEO_PROVIDER", "replicate").strip().lower()
    if provider == "replicate":
        return _generate_replicate_video(prompt, path, aspect_ratio)
    if provider == "openvino":
        model_dir = os.getenv("OPENVINO_LTX_MODEL_DIR", "").strip()
        if not model_dir:
            print("  Local AI video skipped: OPENVINO_LTX_MODEL_DIR is not configured.")
            return False
        from core.openvino_video import generate_ltx_video
        return generate_ltx_video(prompt, path, aspect_ratio=aspect_ratio, model_dir=model_dir)
    print(f"  Unsupported AI_VIDEO_PROVIDER: {provider}")
    return False


# Record scene-image provider outcomes without storing prompts or credentials.
from core.monitoring import monitored as _monitored
from core.ffmpeg_utils import PROBE_TIMEOUT, ffprobe_bin
generate_image = _monitored("image", "generate_image", generate_image)
fetch_clip = _monitored("stock_video", "fetch_clip", fetch_clip)
generate_ai_video = _monitored("ai_video", "generate_ai_video", generate_ai_video)
