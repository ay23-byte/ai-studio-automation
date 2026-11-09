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
        out = subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
            "-show_entries","stream=width,height","-of","csv=p=0",str(p)],
            stderr=subprocess.DEVNULL).decode().strip().split(",")
        return int(out[0]) >= 200 and int(out[1]) >= 200
    except: return False

def _write_source_metadata(path, metadata):
    import json
    sidecar = path.with_suffix(path.suffix + ".source.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _cloudflare(prompt, path):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN: return False
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

def _pollinations(prompt, path):
    time.sleep(POLLINATIONS_GAP)
    try:
        r = requests.get("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900]),
            params={"width":768,"height":1344,"model":"flux","nologo":"true",
                    "seed":random.randint(1,10**6)}, timeout=150)
        r.raise_for_status()
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
    except Exception as e: print(f"Pollinations: {e}"); return False

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

def _search_coverr(q):
    """Search Coverr's official API. Returns raw hits; no scraping is used."""
    if not COVERR_API_KEY or "coverr" in _DISABLED_VIDEO_PROVIDERS:
        return []
    for attempt in range(3):
        try:
            response = requests.get(
                "https://api.coverr.co/videos",
                headers={"x-api-key": COVERR_API_KEY},
                params={"query": q, "page_size": 20, "urls": "true"},
                timeout=30,
            )
            if response.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            if response.status_code in (401, 403):
                _DISABLED_VIDEO_PROVIDERS.add("coverr")
                print(f"  Coverr disabled for this run: HTTP {response.status_code}")
                return []
            if response.status_code in (400, 404):
                print(f"  Coverr query unsupported (HTTP {response.status_code}); skipping retries")
                return []
            response.raise_for_status()
            payload = response.json()
            hits = payload.get("hits", [])
            return hits if isinstance(hits, list) else []
        except Exception as exc:
            print(f"  Coverr search attempt {attempt + 1} failed: {exc}")
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
    width = int(hit.get("width") or hit.get("video_width") or 0)
    height = int(hit.get("height") or hit.get("video_height") or 0)
    asset_id = hit.get("id") or hit.get("video_id") or hit.get("slug")
    if not asset_id:
        return None

    page_url = str(hit.get("url") or hit.get("page_url") or "")
    if page_url and page_url.startswith("/"):
        page_url = "https://coverr.co" + page_url
    metadata = {
        "provider": "Coverr",
        "asset_id": str(asset_id),
        "title": title,
        "description": description,
        "tags": tags_text,
        "page_url": page_url or "https://coverr.co/",
        "query": query,
        "relevance_score": relevance,
        "width": width,
        "height": height,
        "licence": "Coverr License",
        "licence_url": "https://coverr.co/license",
        "attribution_text": f"{title or 'Video'} — Coverr; {page_url or 'https://coverr.co/'}",
        "date_accessed": date.today().isoformat(),
        "terms_reviewed": False,
        "human_visual_inspection_required": True,
        "visual_checks": {
            "steady_clean": "not_automatically_verified",
            "watermark_logo": "not_automatically_verified",
            "private_person_privacy": "not_automatically_verified",
        },
    }
    return video_url, metadata


def _search_pixabay(q):
    # Pixabay's q parameter is limited to 100 characters. Enforce this here
    # as well as at query construction so future callers cannot break builds.
    q = _compact_search_query(q, 100)
    if not q:
        return []
    if not PIXABAY_API_KEY:
        print("  Pixabay skipped: PIXABAY_API_KEY is not configured.")
        return []
    for attempt in range(3):
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                params={"key":PIXABAY_API_KEY,"q":q,"per_page":30,
                        "safesearch":"true","video_type":"film"}, timeout=30)
            if r.status_code == 429:
                print("  Pixabay rate limited (HTTP 429); retrying.")
                time.sleep(20*(attempt+1))
                continue
            if r.status_code in (401, 403):
                _DISABLED_VIDEO_PROVIDERS.add("pixabay")
                print(f"  Pixabay disabled for this run: HTTP {r.status_code}; check the repository secret.")
                return []
            if r.status_code != 200:
                # Pixabay returns actionable JSON error messages for invalid keys,
                # unsupported parameters and exhausted quotas. Keep the diagnostic
                # short and never print the request URL (which contains the API key).
                try:
                    error_payload = r.json()
                    detail = error_payload.get("message") or error_payload.get("error") or str(error_payload)
                except ValueError:
                    detail = " ".join((r.text or "")[:240].split())
                print(f"  Pixabay search failed: HTTP {r.status_code}: {str(detail)[:300]}; query skipped.")
                return []
            payload = r.json()
            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            if not isinstance(hits, list):
                print("  Pixabay returned an unexpected response shape.")
                return []
            return hits
        except Exception as exc:
            print(f"  Pixabay search attempt {attempt + 1} failed: {type(exc).__name__}: {exc}")
    return []



def fetch_clip(keywords, path, used, scene_prompt=""):
    """Search archives, Coverr and Pixabay.

    Mixkit is handled through explicit human-selected local assets because its
    published terms prohibit scripted/bot mass downloading and site scraping.
    """
    from datetime import date
    queries = []
    for value in list(keywords or []) + [scene_prompt]:
        query = _compact_search_query(value, 100)
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= 8:
            break

    try:
        from core.footage_sources import fetch_documentary_clip, _is_archival_video_request
        archival_request = _is_archival_video_request(queries, scene_prompt)
        if fetch_documentary_clip(queries, path, used, scene_prompt=scene_prompt):
            return True
        if archival_request:
            # Do not substitute generic stock or AI-generated footage when the
            # storyboard explicitly calls for real archival moving images.
            print("  archival video unavailable; stock/AI-video substitution refused")
            return False
    except Exception as exc:
        print(f"  public-domain/CC archive search unavailable: {exc}")
        import re
        if re.search(r"\\b(archival|archive footage|newsreel|historical footage|period film|historical film)\\b",
                     str(scene_prompt or ""), re.I):
            print("  archival video search failed; stock/AI-video substitution refused")
            return False

    candidates = []
    for query in queries:
        for hit in _search_coverr(query):
            item = _coverr_candidate(hit, query)
            if item:
                url, metadata = item
                candidates.append((url, metadata, hit.get("id"), "Coverr"))
        for hit in _search_pixabay(query):
            if ("Pixabay:" + str(hit.get("id"))) in used or (hit.get("duration") or 0) < 3:
                continue
            tags = hit.get("tags", "")
            match = max(_relevance(query, tags), _relevance(scene_prompt, tags))
            variants = hit.get("videos") or {}
            best = max(variants.values(), key=lambda v: (v.get("width") or 0) * (v.get("height") or 0), default={})
            width, height = int(best.get("width") or 0), int(best.get("height") or 0)
            if width < 720 or height < 400 or not best.get("url"):
                continue
            metadata = {
                "provider": "Pixabay", "asset_id": hit.get("id"),
                "title": tags, "creator": str(hit.get("user") or ""),
                "page_url": str(hit.get("pageURL") or ""), "query": query,
                "relevance_score": match, "width": width, "height": height,
                "licence": "Pixabay Content License",
                "licence_url": "https://pixabay.com/service/license-summary/",
                "attribution_text": f"{tags} by {hit.get('user') or 'Pixabay contributor'} — Pixabay Content License; {hit.get('pageURL') or ''}",
                "date_accessed": date.today().isoformat(),
                "human_visual_inspection_required": True,
                "visual_checks": {"steady_clean": "not_automatically_verified",
                                  "watermark_logo": "not_automatically_verified",
                                  "private_person_privacy": "not_automatically_verified"},
            }
            candidates.append((best["url"], metadata, hit.get("id"), "Pixabay"))

    candidates.sort(key=lambda item: (
        float(item[1].get("relevance_score") or 0),
        int(item[1].get("width") or 0) * int(item[1].get("height") or 0)
    ), reverse=True)
    for url, metadata, asset_id, provider in candidates[:20]:
        if (provider + ":" + str(asset_id)) in used or float(metadata.get("relevance_score") or 0) < 0.45:
            continue
        metadata["date_accessed"] = metadata.get("date_accessed") or date.today().isoformat()
        try:
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with path.open("wb") as target:
                    for chunk in resp.iter_content(1024 * 1024):
                        if chunk:
                            target.write(chunk)
            if path.stat().st_size > 50_000 and _video_ok(path):
                used.add(f"{provider}:{asset_id}")
                _write_source_metadata(path, metadata)
                print(f"  clip candidate ok ({provider}, relevance {metadata['relevance_score']:.2f}); human visual review required")
                return True
            path.unlink(missing_ok=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            print(f"  {provider} download failed: {exc}")
    print("  no stock clip met the relevance/quality gate across configured sources")
    return False


def _video_ok(path):
    """Accept only a real, non-trivial video file from the remote provider."""
    try:
        if path.stat().st_size < 50_000:
            return False
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)], stderr=subprocess.DEVNULL
        ).decode("utf-8")
        import json
        data = json.loads(out)
        duration = float((data.get("format") or {}).get("duration") or 0)
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        return bool(video and duration >= 2 and int(video.get("width") or 0) >= 256
                    and int(video.get("height") or 0) >= 256)
    except Exception:
        return False


def generate_ai_video(prompt, path):
    """
    Optional Replicate text-to-video fallback.
    It is deliberately opt-in: both REPLICATE_API_TOKEN and
    AI_VIDEO_ENABLED=1 are required. Model-specific input can be supplied as
    JSON in REPLICATE_VIDEO_INPUT_JSON; the prompt key is always overwritten.
    """
    import json, os, re
    from urllib.parse import urlparse

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    model = os.getenv("REPLICATE_VIDEO_MODEL", "").strip()
    enabled = os.getenv("AI_VIDEO_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
    if not (enabled and token and model):
        return False

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model):
        print("AI video skipped: REPLICATE_VIDEO_MODEL must be owner/model.")
        return False

    try:
        raw_input = os.getenv("REPLICATE_VIDEO_INPUT_JSON", "").strip()
        # MiniMax H3 Lite's documented text-to-video input requires only
        # the prompt. Keep model-specific optional fields out of the default
        # request; users can add supported fields through the JSON setting.
        model_input = json.loads(raw_input) if raw_input else {
            "prompt": prompt[:1800]
        }
        if not isinstance(model_input, dict):
            raise ValueError("REPLICATE_VIDEO_INPUT_JSON must be a JSON object.")
        model_input["prompt"] = prompt[:1800]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        response = requests.post(
            f"https://api.replicate.com/v1/models/{model}/predictions",
            headers={**headers, "Prefer": "wait=5"},
            json={"input": model_input}, timeout=45
        )
        response.raise_for_status()
        prediction = response.json()
        deadline = time.time() + int(os.getenv("AI_VIDEO_TIMEOUT_SECONDS", "480"))
        while prediction.get("status") in {"starting", "processing"} and time.time() < deadline:
            time.sleep(5)
            get_url = (prediction.get("urls") or {}).get("get")
            if not get_url:
                break
            poll = requests.get(get_url, headers=headers, timeout=30)
            poll.raise_for_status()
            prediction = poll.json()

        if prediction.get("status") != "succeeded":
            print("AI video provider did not complete: " + str(prediction.get("error") or prediction.get("status")))
            return False

        output = prediction.get("output")
        candidates = output if isinstance(output, list) else [output]
        video_url = next((item for item in candidates if isinstance(item, str)
                          and urlparse(item).scheme == "https"), None)
        if not video_url:
            print("AI video provider returned no downloadable video URL.")
            return False
        with requests.get(video_url, stream=True, timeout=120) as download:
            download.raise_for_status()
            with path.open("wb") as target:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        target.write(chunk)
        if not _video_ok(path):
            path.unlink(missing_ok=True)
            print("AI video provider returned an invalid or too-short video.")
            return False
        print("  clip ok (Replicate AI video)")
        return True
    except Exception as exc:
        print(f"AI video generation failed: {exc}")
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


# Record scene-image provider outcomes without storing prompts or credentials.
from core.monitoring import monitored as _monitored
generate_image = _monitored("image", "generate_image", generate_image)
fetch_clip = _monitored("stock_video", "fetch_clip", fetch_clip)
generate_ai_video = _monitored("ai_video", "generate_ai_video", generate_ai_video)
