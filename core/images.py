import time, random, base64, urllib.parse, subprocess
from pathlib import Path
import requests
from core.config import CF_ACCOUNT_ID, CF_API_TOKEN, POLLINATIONS_GAP, PIXABAY_API_KEY, IMAGE_STYLE

def _ok(p):
    try:
        if p.stat().st_size < 10_000: return False
        out = subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
            "-show_entries","stream=width,height","-of","csv=p=0",str(p)],
            stderr=subprocess.DEVNULL).decode().strip().split(",")
        return int(out[0]) >= 200 and int(out[1]) >= 200
    except: return False

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
            path.write_bytes(data); return _ok(path)
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
        path.write_bytes(r.content); return _ok(path)
    except Exception as e: print(f"Pollinations: {e}"); return False

def generate_image(scene_prompt, path):
    full = IMAGE_STYLE + scene_prompt
    if _cloudflare(full, path): print(f"  image ok (cloudflare)"); return True
    if _pollinations(full, path): print(f"  image ok (pollinations)"); return True
    print(f"  image failed: {scene_prompt[:50]}"); return False

def _search_pixabay(q):
    if not PIXABAY_API_KEY: return []
    for attempt in range(3):
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                params={"key":PIXABAY_API_KEY,"q":q,"per_page":30,
                        "safesearch":"true","video_type":"film"}, timeout=30)
            if r.status_code == 429: time.sleep(20*(attempt+1)); continue
            if r.status_code != 200: return []
            return r.json().get("hits", [])
        except: pass
    return []

def fetch_clip(keywords, path, used):
    for q in keywords:
        time.sleep(1.2)
        hits = _search_pixabay(q)
        fresh = [h for h in hits if h.get("id") not in used and (h.get("duration") or 0) >= 3]
        # Pixabay already filters by the query. Prefer usable, higher-resolution,
        # longer clips instead of randomising the result set.
        candidates = fresh or [h for h in hits if (h.get("duration") or 0) >= 3]
        candidates.sort(
            key=lambda h: (
                max((v.get("width") or 0) * (v.get("height") or 0)
                    for v in (h.get("videos") or {}).values()),
                min(h.get("duration") or 0, 60),
                h.get("views") or 0,
            ),
            reverse=True,
        )
        for h in candidates[:5]:
            url = None
            for s in ("small","medium","tiny"):
                v = h.get("videos",{}).get(s,{})
                if v.get("url") and (v.get("size") or 0) < 60_000_000:
                    url = v["url"]; break
            if not url: continue
            try:
                with requests.get(url, stream=True, timeout=120) as resp:
                    resp.raise_for_status(); path.write_bytes(resp.content)
                if path.stat().st_size > 50_000:
                    used.add(h.get("id")); print(f"  clip ok (pixabay '{q}')"); return True
            except Exception as e: print(f"  clip download: {e}")
    return False


# Record scene-image provider outcomes without storing prompts or credentials.
from core.monitoring import monitored as _monitored
_generate_image_unmonitored = generate_image
generate_image = _monitored("image", "generate_image", _generate_image_unmonitored)
_fetch_clip_unmonitored = fetch_clip
fetch_clip = _monitored("stock_video", "fetch_clip", _fetch_clip_unmonitored)
