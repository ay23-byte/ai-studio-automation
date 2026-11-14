"""Documentary footage discovery from public archives and media libraries.

Adapters for NASA, Wikimedia Commons, Internet Archive, Library of Congress,
DVIDS, Europeana and NARA. Each adapter returns ``(download_url, metadata)``
leads built only from what the provider's API returned for that exact item.

A lead is not footage. ``_download`` fetches the file, inspects it with ffprobe
and writes a JSON provenance sidecar beside it; only then can the engine in
``core.footage_engine`` select it. Every asset still needs the human rights and
context review recorded by the review package.
"""
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from core.ffmpeg_utils import ffmpeg_bin
from core.footage_report import active_scope, redact

# (connect, read) seconds for metadata requests.
TIMEOUT = (10, 30)
MAX_ATTEMPTS = 3
# Authorization failures are stable for the lifetime of one production run.
_DISABLED_PROVIDER_HOSTS = set()
# Why each host was switched off, for provider_report.json.
_HOST_DISABLED_REASON = {}
# Hosts that could not be reached at all (DNS/TLS/timeout) after retries. Two
# such failures open the circuit so one unreachable archive cannot add minutes
# of retries to every remaining shot of the run.
_HOST_NETWORK_FAILURES = {}
NETWORK_FAILURE_LIMIT = 2
# Wikimedia's User-Agent policy asks for a client name and a way to reach the
# operator; a generic agent is throttled. FOOTAGE_CONTACT overrides the contact.
PROJECT_URL = "https://github.com/ay23-byte/ai-studio-automation"
USER_AGENT = (
    "AIStudioDocumentary/2.0 ("
    + (os.getenv("FOOTAGE_CONTACT", "").strip() or PROJECT_URL)
    + "; documentary footage research) python-requests"
)

# Minimum seconds between two metadata requests to one host. The Library of
# Congress publishes 20 requests per minute for its JSON API and blocks a client
# for one hour when that is exceeded, so it is paced below that rate.
HOST_MIN_INTERVAL = {
    "www.loc.gov": 3.2,
    "api.europeana.eu": 0.4,
    "commons.wikimedia.org": 0.3,
    "archive.org": 0.3,
    "api.dvidshub.net": 0.3,
    "images-api.nasa.gov": 0.2,
    "catalog.archives.gov": 0.3,
}
HOST_PROVIDER = {
    "images-api.nasa.gov": "NASA", "images-assets.nasa.gov": "NASA",
    "commons.wikimedia.org": "Wikimedia Commons",
    "archive.org": "Internet Archive",
    "www.loc.gov": "Library of Congress",
    "api.dvidshub.net": "DVIDS",
    "api.europeana.eu": "Europeana",
    "catalog.archives.gov": "National Archives (NARA)",
}
# What the provider's own documentation says a refused request means.
AUTH_FAILURE_HINT = {
    "api.dvidshub.net": (
        "DVIDS answers 403 when api_key is missing, invalid, or used from an origin other than the one "
        "registered for the key. Check DVIDS_API_KEY and the origin registered in the DVIDS account; "
        "set DVIDS_API_REFERER to that registered origin if the key is origin-bound."),
    "api.europeana.eu": (
        "Europeana answers 401 when the key is missing or not accepted. Since 28 May 2025 keys are issued "
        "from the Europeana account page (Manage API keys); a key from the old registration form may no "
        "longer work. Check EUROPEANA_API_KEY."),
    "catalog.archives.gov": "NARA Catalog API v2 needs a valid x-api-key; check NARA_API_KEY.",
}

_STATE_LOCK = threading.RLock()
_HOST_LOCKS = {}
_HOST_LAST_REQUEST = {}
# Responses already fetched in this run. Expanded queries repeat across scenes,
# and a repeated request costs quota for the same answer.
_JSON_CACHE = {}
_JSON_CACHE_LIMIT = 2000


def _env_number(name, default):
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return float(default)


def reset_run_state():
    """Forget circuit breakers, pacing and cached responses (new run or test)."""
    with _STATE_LOCK:
        _DISABLED_PROVIDER_HOSTS.clear()
        _HOST_DISABLED_REASON.clear()
        _HOST_NETWORK_FAILURES.clear()
        _HOST_LAST_REQUEST.clear()
        _JSON_CACHE.clear()
        _DVIDS_RUN.update(headers=None, legacy_tried=False)
    stock = sys.modules.get("core.images")
    if stock is not None and hasattr(stock, "reset_stock_state"):
        stock.reset_stock_state()


def _host_of(url):
    return url.split("/")[2].lower() if "://" in str(url) else ""


def _record(host, **event):
    """Send one HTTP event to the active provider report, if a run is recording."""
    name, report = active_scope()
    if report is None:
        return
    name = name or HOST_PROVIDER.get(host, host or "unknown host")
    status = event.get("status")
    if status is not None:
        report.http_status(name, status)
    if event.get("retry"):
        report.add(name, "retries")
    if event.get("failure"):
        report.fail(name, event["failure"], search_status=event.get("search_status"))


def _disable_host(host, reason):
    with _STATE_LOCK:
        _DISABLED_PROVIDER_HOSTS.add(host)
        _HOST_DISABLED_REASON[host] = redact(reason)[:400]


def _retry_after_seconds(response):
    """Seconds the server asked us to wait, from Retry-After (number or HTTP date)."""
    try:
        raw = response.headers.get("Retry-After", "")
    except Exception:
        return None
    if raw is None or isinstance(raw, (int, float)):
        return float(raw) if raw is not None else None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _backoff_delay(attempt, host=""):
    """Exponential backoff with jitter: 1s, 2s, 4s ... plus up to one base interval."""
    base = max(1.0, HOST_MIN_INTERVAL.get(host, 0.0))
    cap = _env_number("FOOTAGE_MAX_BACKOFF_SECONDS", 20)
    return min(cap, base * (2 ** attempt)) + random.uniform(0.0, base)


def _pace(host):
    """Wait until this host's minimum request interval has passed."""
    interval = HOST_MIN_INTERVAL.get(host, 0.0) * _env_number("FOOTAGE_PACING_FACTOR", 1.0)
    if interval <= 0:
        return
    with _STATE_LOCK:
        lock = _HOST_LOCKS.setdefault(host, threading.Lock())
    with lock:
        wait = _HOST_LAST_REQUEST.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            # threading.Event keeps pacing separate from the retry sleeps.
            threading.Event().wait(wait)
        _HOST_LAST_REQUEST[host] = time.monotonic()


def _cache_key(url, params, headers):
    def freeze(value):
        if isinstance(value, dict):
            return tuple(sorted((str(k), freeze(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(freeze(v) for v in value)
        return str(value)
    return (url, freeze(params or {}), tuple(sorted(str(k).lower() for k in (headers or {}))))


def _get_json(url, params=None, headers=None):
    """Fetch provider metadata with pacing, bounded retries and a per-run circuit breaker.

    * 401/403: the provider is switched off for the run and the documented cause is recorded.
    * 429 and 5xx: retried with exponential backoff and jitter. ``Retry-After`` is
      honoured; when the server asks for a longer wait than the run can afford, the
      provider is switched off instead of being asked again early.
    * 404 and other client errors: this request returns nothing, the provider stays on.
    * Every outcome is recorded for ``provider_report.json``.
    """
    host = _host_of(url)
    if host in _DISABLED_PROVIDER_HOSTS:
        return None
    key = _cache_key(url, params, headers)
    with _STATE_LOCK:
        if key in _JSON_CACHE:
            return _JSON_CACHE[key]
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    max_retry_after = _env_number("FOOTAGE_MAX_RETRY_AFTER_SECONDS", 30)
    _pace(host)
    for attempt in range(MAX_ATTEMPTS):
        last_attempt = attempt >= MAX_ATTEMPTS - 1
        try:
            response = requests.get(
                url, params=params, headers=request_headers, timeout=TIMEOUT
            )
            status = response.status_code
            _record(host, status=status)
            if status in (401, 403):
                hint = AUTH_FAILURE_HINT.get(host, "check API access and credentials.")
                reason = f"HTTP {status}: {hint}"
                _disable_host(host, reason)
                _record(host, failure=reason, search_status="auth_rejected")
                print(f"  footage provider disabled for this run ({host}): HTTP {status}; {hint}")
                return None
            if status == 429 or status in (500, 502, 503, 504):
                kind = "rate limited" if status == 429 else "server error"
                asked = _retry_after_seconds(response)
                if asked is not None and asked > max_retry_after:
                    reason = (f"HTTP {status} ({kind}); the provider asked for a {asked:.0f}s wait, longer than "
                              f"the {max_retry_after:.0f}s this run allows, so it is skipped for the rest of the run")
                    _disable_host(host, reason)
                    _record(host, failure=reason, search_status="rate_limited" if status == 429 else "server_error")
                    print(f"  footage provider paused for this run ({host}): {reason}.")
                    return None
                if not last_attempt:
                    delay = max(asked or 0.0, _backoff_delay(attempt, host))
                    _record(host, retry=True)
                    print(f"  footage provider transient HTTP {status} ({host}); "
                          f"retry {attempt + 1}/{MAX_ATTEMPTS - 1} after {delay:.1f}s.")
                    time.sleep(delay)
                    continue
                reason = f"HTTP {status} ({kind}) after {MAX_ATTEMPTS} attempts"
                _disable_host(host, reason)
                _record(host, failure=reason, search_status="rate_limited" if status == 429 else "server_error")
                print(f"  footage provider unavailable for rest of this run ({host}): HTTP {status} after retries.")
                return None
            if status >= 400:
                reason = f"HTTP {status} for this request"
                _record(host, failure=reason, search_status="http_error")
                print(f"  footage request refused ({host}): HTTP {status}; this query is skipped.")
                return None
            _HOST_NETWORK_FAILURES.pop(host, None)
            content_type = str(response.headers.get("content-type", ""))
            if "json" not in content_type.lower():
                preview = " ".join(str(response.text)[:180].split())
                looks_blocked = bool(re.search(r"captcha|are you a robot|access denied|rate limit", preview, re.I))
                reason = (f"expected JSON, received {content_type or 'unknown content type'}"
                          + ("; the reply looks like a block or CAPTCHA page" if looks_blocked else ""))
                if looks_blocked:
                    _disable_host(host, reason)
                _record(host, failure=reason, search_status="blocked" if looks_blocked else "invalid_response")
                print(f"  footage discovery unavailable ({host}): {reason}; response begins: {preview!r}")
                return None
            try:
                data = response.json()
            except ValueError as exc:
                _record(host, failure=f"invalid JSON response ({exc})", search_status="invalid_response")
                print(f"  footage discovery unavailable ({host}): invalid JSON response ({exc}).")
                return None
            with _STATE_LOCK:
                if len(_JSON_CACHE) >= _JSON_CACHE_LIMIT:
                    _JSON_CACHE.clear()
                _JSON_CACHE[key] = data
            return data
        except requests.RequestException as exc:
            if not last_attempt:
                delay = _backoff_delay(attempt, host)
                _record(host, retry=True)
                print(f"  footage provider request failed ({host}): {type(exc).__name__}; "
                      f"retry {attempt + 1}/{MAX_ATTEMPTS - 1} after {delay:.1f}s.")
                time.sleep(delay)
                continue
            failures = _HOST_NETWORK_FAILURES.get(host, 0) + 1
            _HOST_NETWORK_FAILURES[host] = failures
            detail = redact(str(exc).split("(Caused by")[0].strip())[:160]
            timed_out = isinstance(exc, requests.Timeout)
            reason = f"{type(exc).__name__}: {detail}"
            _record(host, failure=reason, search_status="timeout" if timed_out else "network_error")
            if host and failures >= NETWORK_FAILURE_LIMIT:
                _disable_host(host, f"unreachable after repeated attempts ({type(exc).__name__})")
                print(f"  footage provider unreachable ({host}); skipping it for the rest of this run: {type(exc).__name__}")
            else:
                print(f"  footage discovery unavailable ({host or 'unknown host'}): {reason}")
            return None
        except Exception as exc:
            reason = redact(f"{type(exc).__name__}: {exc}")
            _record(host, failure=reason, search_status="error")
            print(f"  footage discovery unavailable ({host or 'unknown host'}): {reason}")
            return None
    return None


def _valid_video(path):
    """True when the file is real, decodable footage (see ``core.footage_validation``)."""
    try:
        from core.footage_validation import validate_media
        return bool(validate_media(Path(path), with_hash=False)["ok"])
    except Exception:
        return False


def _video_dimensions(path):
    try:
        from core.footage_validation import inspect_media
        facts = inspect_media(Path(path))
        return int(facts.get("width") or 0), int(facts.get("height") or 0)
    except Exception:
        return 0, 0


def clip_size_limit_mb():
    """Largest file fetched for one scene: ``FOOTAGE_MAX_CLIP_MB`` (250)."""
    return max(1.0, _env_number("FOOTAGE_MAX_CLIP_MB", 250))


def _secure_url(url):
    """Return an https URL, upgrading plain http; '' when the value is not a web URL."""
    text = str(url or "").strip()
    if text.startswith("http://"):
        text = "https://" + text[len("http://"):]
    return text if text.startswith("https://") else ""


def _canonical_media_url(url):
    """Drop tracking parameters so one file is recognised under different links."""
    parts = urlsplit(str(url or ""))
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _has_video_suffix(url, suffixes=(".mp4", ".mov", ".m4v", ".webm", ".ogv")):
    return urlsplit(str(url or "")).path.lower().endswith(tuple(suffixes))


def _plain(value, limit=600):
    """Provider text without markup, collapsed and bounded."""
    def flatten(item):
        # Providers nest lists (and empty objects) inside list fields; join the text, drop the rest.
        if isinstance(item, (list, tuple)):
            return " ".join(part for part in (flatten(child) for child in item) if part)
        if isinstance(item, dict):
            return " ".join(part for part in (flatten(child) for child in item.values()) if part)
        return str(item) if item not in (None, "") else ""

    text = re.sub(r"<[^>]+>", " ", flatten(value))
    return " ".join(text.split())[:limit]


def _first(value):
    if isinstance(value, (list, tuple)):
        return next((item for item in value if item), "")
    return value or ""


def _search_pages():
    """Result pages requested per query: ``FOOTAGE_SEARCH_PAGES`` (1, at most 3)."""
    return int(min(3, max(1, _env_number("FOOTAGE_SEARCH_PAGES", 1))))


def _detail_limit():
    """Search hits whose item record is fetched per query: ``FOOTAGE_DETAIL_LOOKUPS`` (3)."""
    return int(min(8, max(1, _env_number("FOOTAGE_DETAIL_LOOKUPS", 3))))


def _lead_relevance(query, *texts):
    """Cheap word overlap used only to decide which hits deserve an item lookup."""
    wanted = _tokens(query)
    found = set()
    for text in texts:
        found |= _tokens(_plain(text, 2000))
    return len(wanted & found) / len(wanted) if wanted else 0.0


def _download(url, path, metadata):
    """Fetch one candidate, validate the file itself, and write its provenance sidecar.

    Returns ``True`` only when the downloaded file passed validation. The reason
    for any rejection is stored in ``metadata["validation"]`` for the report.
    """
    path = Path(path)
    urls = [_secure_url(url)] + [_secure_url(item) for item in (metadata.get("alternate_urls") or [])]
    urls = [item for item in dict.fromkeys(urls) if item]
    if not urls:
        metadata["validation"] = {"ok": False, "status": "no_media_url",
                                  "reason": "the provider gave no https media URL"}
        return False
    from core.downloads import DownloadError, fetch
    from core.footage_validation import validate_media
    last = {"ok": False, "status": "download_failed", "reason": "not attempted"}
    failures = []  # one line per file tried, so the first failure is not hidden by the last

    def failed(media_url, status, reason):
        name = _canonical_media_url(media_url).rstrip("/").split("/")[-1][:60]
        failures.append(f"{name}: {reason}")
        outcome = {"ok": False, "status": status, "reason": redact(" | ".join(failures))[:500]}
        metadata["validation"] = outcome
        return outcome

    for index, media_url in enumerate(urls):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if ".m3u8" in media_url.lower():
                subprocess.run([
                    ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(media_url),
                    "-c", "copy", "-bsf:a", "aac_adtstoasc", str(path)
                ], check=True, timeout=180)
            else:
                fetch(media_url, path, kind="video", headers={"User-Agent": USER_AGENT},
                      timeout=(20, 120), max_mb=clip_size_limit_mb())
        except subprocess.CalledProcessError as exc:
            path.unlink(missing_ok=True)
            last = failed(media_url, "download_failed", f"FFmpeg could not read the stream (exit {exc.returncode})")
            print(f"  footage download failed ({metadata.get('provider')}): {last['reason']}")
            continue
        except subprocess.TimeoutExpired:
            path.unlink(missing_ok=True)
            last = failed(media_url, "download_failed", "FFmpeg did not finish reading the stream in 180s")
            print(f"  footage download failed ({metadata.get('provider')}): {last['reason']}")
            continue
        except (DownloadError, subprocess.SubprocessError, OSError) as exc:
            path.unlink(missing_ok=True)
            last = failed(media_url, "download_failed", redact(str(exc))[:200])
            print(f"  footage download failed ({metadata.get('provider')}): {last['reason']}")
            continue
        except Exception as exc:
            path.unlink(missing_ok=True)
            last = failed(media_url, "download_failed", redact(f"{type(exc).__name__}: {exc}")[:200])
            print(f"  footage download failed ({metadata.get('provider')}): {last['reason']}")
            continue
        result = validate_media(path)
        if not result["ok"]:
            path.unlink(missing_ok=True)
            last = dict(result, reason=failed(media_url, result["status"], result["reason"])["reason"])
            print(f"  footage file rejected ({metadata.get('provider')}): {result['status']}: {result['reason']}")
            continue
        metadata["width"], metadata["height"] = result["width"], result["height"]
        metadata["duration_seconds"] = result["duration_seconds"]
        metadata["file_size_bytes"] = result["size_bytes"]
        metadata["media_sha256"] = result.get("sha256", "")
        metadata["media_type"] = "video"
        metadata["container"] = result["container"]
        metadata["video_codec"] = result["video_codec"]
        metadata["fps"] = result["fps"]
        metadata["download_url"] = redact(_canonical_media_url(media_url))
        metadata["download_timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        metadata["local_path"] = str(path)
        metadata["rendition_index"] = index
        metadata["validation"] = {
            "ok": True, "status": "valid", "reason": result["reason"], "tool": "ffprobe",
            "checked_at": metadata["download_timestamp"],
            "facts": {key: result[key] for key in (
                "container", "video_codec", "width", "height", "duration_seconds", "fps",
                "size_bytes", "has_audio") if key in result},
        }
        metadata.pop("alternate_urls", None)
        from core.asset_licensing import score_asset
        score = score_asset(metadata, result["width"], result["height"])
        metadata["quality_score"] = score["score_0_10"]
        metadata["quality_score_details"] = score
        if score["score_0_10"] < 3.0:
            path.unlink(missing_ok=True)
            metadata["validation"] = {"ok": False, "status": "low_score",
                                      "reason": f"asset score {score['score_0_10']}/10 is below 3"}
            print(f"  archive candidate rejected: score {score['score_0_10']}/10 is below 3")
            return False
        sidecar = path.with_suffix(path.suffix + ".source.json")
        sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  clip ok ({metadata.get('provider')}, {result['width']}x{result['height']}, "
              f"{result['duration_seconds']:.0f}s); licence review required")
        return True
    metadata["validation"] = last
    return False


# ---------------------------------------------------------------------------
# Provider adapters. Each returns [(media_url, metadata), ...] built only from
# what the provider's API returned for that exact item.
# ---------------------------------------------------------------------------
NASA_LICENCE_URL = "https://www.nasa.gov/nasa-brand-center/images-and-media/"
DVIDS_TERMS_URL = "https://api.dvidshub.net/docs/tos"
_THIRD_PARTY_MARKERS = re.compile(
    r"copyright|\u00a9|courtesy of|provided by|licensed from|all rights reserved|"
    r"\b(getty|ap|associated press|reuters|bbc|universal|disney|shutterstock|pond5)\b", re.I)


def _count(field, amount=1):
    """Add to a counter of the provider that is currently being searched."""
    name, report = active_scope()
    if report is not None and name:
        report.add(name, field, amount)


def _reject(reason, *, licence=True):
    """Record why a search result is not offered. ``licence=False`` for non-rights reasons."""
    name, report = active_scope()
    if report is not None and name:
        report.reject(name, reason, licence=licence)


def _accept(metadata):
    """Classify the licence once, record it on the lead, and say whether it may be used."""
    from core.footage_licence import attribution_line, classify_licence
    decision = classify_licence(metadata)
    metadata["licence_state"] = decision["state"]
    metadata["licence_reason"] = decision["reason"]
    metadata["attribution_required"] = decision["attribution_required"]
    metadata["commercial_use_allowed"] = decision["commercial_use"]
    metadata["modification_allowed"] = decision["modification"]
    metadata["share_alike"] = decision["share_alike"]
    metadata.setdefault("date_accessed", date.today().isoformat())
    metadata.setdefault("media_type", "video")
    if not decision["publishable"]:
        _reject(f"licence {decision['state']}: {decision['reason']}"[:140])
        return False
    if not metadata.get("attribution_text"):
        metadata["attribution_text"] = attribution_line(metadata, decision)
    return True


def _nasa_renditions(manifest):
    """Order a NASA asset manifest: full-quality web renditions first, tiny ones last."""
    order = {"large": 0, "orig": 1, "medium": 2, "small": 3, "mobile": 4}
    ranked = []
    for entry in manifest if isinstance(manifest, list) else []:
        if not isinstance(entry, str) or not _has_video_suffix(entry, (".mp4", ".mov", ".m4v")):
            continue
        match = re.search(r"~([a-z]+)\.(?:mp4|mov|m4v)$", entry, re.I)
        kind = match.group(1).lower() if match else "orig"
        if kind in {"thumb", "preview"}:
            continue
        # The manifest lists http:// links and may contain spaces.
        url = _secure_url(quote(entry, safe=":/~%?=&"))
        if url:
            ranked.append((order.get(kind, 5), url))
    return [url for _rank, url in sorted(ranked)]


def _nasa(query):
    """NASA Image and Video Library. No key. One search, then the asset manifest per hit."""
    results = []
    for page in range(1, _search_pages() + 1):
        data = _get_json("https://images-api.nasa.gov/search",
                         {"q": query, "media_type": "video", "page_size": 12, "page": page})
        items = ((data or {}).get("collection") or {}).get("items") or []
        _count("results_found", len(items))
        ranked = sorted(items, key=lambda item: -_lead_relevance(
            query, ((item.get("data") or [{}])[0]).get("title"),
            ((item.get("data") or [{}])[0]).get("description"),
            ((item.get("data") or [{}])[0]).get("keywords")))
        for item in ranked[:_detail_limit() + 2]:
            meta = (item.get("data") or [{}])[0]
            nasa_id = str(meta.get("nasa_id") or "").strip()
            href = item.get("href")
            manifest = _get_json(_secure_url(href) or href) if href else None
            urls = _nasa_renditions(manifest)
            if not urls:
                continue
            rights_text = " ".join(_plain(meta.get(key), 4000) for key in
                                   ("description", "keywords", "center", "photographer", "secondary_creator"))
            # NASA says its own media is generally not copyrighted, but third-party
            # material appears on NASA sites. Require an identifiable NASA centre
            # and reject explicit copyright or third-party credit signals.
            center = str(meta.get("center") or "").strip()
            third_party = _THIRD_PARTY_MARKERS.search(rights_text)
            explicit_public_domain = bool(re.search(r"public domain", rights_text, re.I))
            if third_party and not explicit_public_domain:
                _reject("item text names a third party or copyright holder")
                continue
            if not (center or explicit_public_domain):
                _reject("no NASA centre recorded for the item")
                continue
            metadata = {
                "provider": "NASA", "asset_id": nasa_id or None,
                "title": meta.get("title"), "description": _plain(meta.get("description")),
                "tags": [str(word) for word in (meta.get("keywords") or [])][:20],
                "creator": f"NASA {center}".strip(),
                "date_created": str(meta.get("date_created") or "")[:10],
                "page_url": f"https://images.nasa.gov/details/{quote(nasa_id)}" if nasa_id else "",
                "licence": "Public Domain" if explicit_public_domain else "NASA Media Usage Guidelines",
                "licence_url": NASA_LICENCE_URL,
                "query": query, "alternate_urls": urls[1:4],
                "rights_statement": "NASA media usage guidelines: NASA content is generally not subject to U.S. copyright unless third-party copyright is indicated; credit NASA and inspect all embedded third-party material.",
                "licence_status": "NASA center metadata present and no third-party/copyright marker detected; human review required",
            }
            if _accept(metadata):
                results.append((urls[0], metadata))
        if len(items) < 12:
            break
    return results


def _extmetadata(info, key):
    return _plain(((info.get("extmetadata") or {}).get(key) or {}).get("value"), 1500)


def _wikimedia(query):
    """Wikimedia Commons video files with file-level licence metadata."""
    results, offset = [], None
    limit_bytes = clip_size_limit_mb() * 1024 * 1024
    for _page in range(_search_pages()):
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:video {query}", "gsrnamespace": 6, "gsrlimit": 20,
            "prop": "videoinfo", "viprop": "url|size|mime|extmetadata|derivatives",
        }
        if offset:
            params["gsroffset"] = offset
        data = _get_json("https://commons.wikimedia.org/w/api.php", params)
        pages = list((((data or {}).get("query") or {}).get("pages") or {}).values())
        pages.sort(key=lambda entry: entry.get("index") or 0)
        _count("results_found", len(pages))
        for page in pages:
            info = (page.get("videoinfo") or page.get("imageinfo") or [{}])[0]
            original = _canonical_media_url(_secure_url(info.get("url")))
            mime = str(info.get("mime") or "").lower()
            if not original:
                continue
            if mime and not (mime.startswith("video/") or mime == "application/ogg"):
                continue
            if not mime and not _has_video_suffix(original, (".webm", ".ogv", ".mp4", ".mov")):
                continue
            # Largest web transcodes first; they are smaller and always streamable.
            derivatives = sorted(
                (item for item in (info.get("derivatives") or [])
                 if _secure_url(item.get("src")) and "video" in str(item.get("type") or "")),
                key=lambda item: -int(item.get("height") or 0))
            derivative_urls = [_secure_url(item["src"]) for item in derivatives]
            size = int(info.get("size") or 0)
            if size and size > limit_bytes:
                if not derivative_urls:
                    continue
                urls = derivative_urls
            else:
                urls = [original] + derivative_urls
            licence = _extmetadata(info, "LicenseShortName")
            licence_url = _extmetadata(info, "LicenseUrl")
            if not licence_url and re.fullmatch(r"public domain|pd[- ].*|cc0.*", licence, re.I):
                # Commons records public-domain files without a licence URL; the
                # file page itself carries the rights statement.
                licence_url = str(info.get("descriptionurl") or "")
            restrictions = _extmetadata(info, "Restrictions")
            title = _extmetadata(info, "ObjectName") or re.sub(r"^File:|\.\w{3,4}$", "", str(page.get("title") or ""))
            metadata = {
                "provider": "Wikimedia Commons", "asset_id": str(page.get("pageid") or page.get("title") or ""),
                "title": title, "description": _extmetadata(info, "ImageDescription"),
                "tags": [tag for tag in _extmetadata(info, "Categories").split("|") if tag][:20],
                "creator": _extmetadata(info, "Artist"), "credit": _extmetadata(info, "Credit"),
                "date_created": _extmetadata(info, "DateTimeOriginal")[:40],
                "page_url": str(info.get("descriptionurl") or ""),
                "licence": licence, "licence_url": licence_url, "query": query,
                "usage_terms": _extmetadata(info, "UsageTerms"),
                "declared_width": int(info.get("width") or 0), "declared_height": int(info.get("height") or 0),
                "declared_duration_seconds": float(info.get("duration") or 0),
                "declared_size_bytes": size, "mime": mime, "alternate_urls": urls[1:4],
            }
            if restrictions:
                # Commons uses this for trademark and personality-rights warnings:
                # not a copyright bar, but a person must look at it.
                metadata["restrictions"] = restrictions
                metadata["review_notes"] = f"Commons lists non-copyright restrictions: {restrictions}"
            if _accept(metadata):
                results.append((urls[0], metadata))
        offset = ((data or {}).get("continue") or {}).get("gsroffset")
        if not offset:
            break
    return results


_IA_LICENCE_FILTER = (
    "(licenseurl:*publicdomain* OR licenseurl:*licenses\\/by\\/* OR licenseurl:*licenses\\/by-sa\\/*)")
_IA_VIDEO_SUFFIXES = (".mp4", ".m4v", ".mov", ".ogv", ".webm", ".mpg", ".mpeg")


def _internet_archive_file(files, limit_bytes):
    """Pick the best streamable file under the size limit, plus fallbacks."""
    ranked = []
    for entry in files or []:
        name = str(entry.get("name") or "")
        if not name.lower().endswith(_IA_VIDEO_SUFFIXES) or entry.get("private") in (True, "true"):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > limit_bytes:
            continue
        fmt = str(entry.get("format") or "").lower()
        # h.264 MP4 derivatives decode everywhere; then other MP4, then the rest.
        family = 0 if "h.264" in fmt or name.lower().endswith(".mp4") else (1 if name.lower().endswith((".m4v", ".mov")) else 2)
        ranked.append((family, -size, name, size))
    ranked.sort()
    return [(name, size) for _family, _neg, name, size in ranked]


def _internet_archive(query):
    """Internet Archive items that carry an explicit reusable licence URL."""
    results = []
    limit_bytes = clip_size_limit_mb() * 1024 * 1024
    for page in range(1, _search_pages() + 1):
        data = _get_json("https://archive.org/advancedsearch.php", {
            "q": f"({query}) AND mediatype:(movies) AND {_IA_LICENCE_FILTER}",
            "fl[]": ["identifier", "title", "licenseurl", "creator", "description", "year", "subject"],
            "rows": 15, "page": page, "output": "json",
        })
        docs = ((data or {}).get("response") or {}).get("docs") or []
        _count("results_found", len(docs))
        docs = sorted(docs, key=lambda doc: -_lead_relevance(
            query, doc.get("title"), doc.get("description"), doc.get("subject")))
        for doc in docs[:_detail_limit()]:
            identifier = str(doc.get("identifier") or "").strip()
            if not identifier:
                continue
            detail = _get_json(f"https://archive.org/metadata/{quote(identifier)}")
            if not detail or detail.get("is_dark"):
                continue
            item = detail.get("metadata") or {}
            if str(item.get("access-restricted-item") or "").lower() == "true":
                _reject("item is access-restricted")
                continue
            licence_url = str(_first(item.get("licenseurl")) or _first(doc.get("licenseurl")) or "").strip()
            from core.footage_licence import parse_creative_commons
            parsed = parse_creative_commons(licence_url) if licence_url else None
            choices = _internet_archive_file(detail.get("files"), limit_bytes)
            if not choices:
                continue
            page_url = f"https://archive.org/details/{quote(identifier)}"
            urls = [f"https://archive.org/download/{quote(identifier)}/{quote(name)}" for name, _size in choices]
            metadata = {
                "provider": "Internet Archive", "asset_id": identifier,
                "title": _plain(_first(item.get("title")) or doc.get("title"), 300),
                "description": _plain(item.get("description") or doc.get("description")),
                "tags": [str(word) for word in ([item.get("subject")] if isinstance(item.get("subject"), str)
                                                else (item.get("subject") or []))][:20],
                "creator": _plain(_first(item.get("creator")) or _first(doc.get("creator")), 200),
                "date_created": str(_first(item.get("date")) or _first(item.get("year")) or doc.get("year") or "")[:20],
                "page_url": page_url,
                "licence": (parsed or {}).get("label", ""), "licence_url": licence_url,
                "query": query, "declared_size_bytes": choices[0][1], "alternate_urls": urls[1:4],
                "rights_statement": _plain(item.get("rights") or item.get("possible-copyright-status"), 600),
            }
            if _accept(metadata):
                results.append((urls[0], metadata))
        if len(docs) < 15:
            break
    return results


_LOC_RESTRICTIVE = re.compile(
    r"rights? (?:are )?restricted|permission (?:is )?required|may be (?:protected|restricted)|"
    r"in copyright|copyrighted|not (?:be )?reproduced|publication .{0,40}restricted|"
    r"no (?:reproduction|commercial)|rights? (?:status )?not evaluated|donor restrictions", re.I)
_LOC_NO_KNOWN = re.compile(
    r"no known (?:copyright )?restrictions|not aware of any (?:u\.?s\.? )?copyright|"
    r"public domain|no (?:u\.?s\.? )?copyright restrictions", re.I)


# The Library attaches one general rights text to every film of a collection. Its
# sentences about "rare cases", "some titles" and "some works" describe the collection,
# not the item, so they must not be read as a restriction on the item itself.
_LOC_GENERAL_SENTENCE = re.compile(
    r"in rare cases|for example|some titles|some works|in the vast majority|"
    r"rights assessment is your responsibility|materials not in the public domain|"
    r"whenever possible|you should (?:also )?consult|this catalog data|more about copyright|"
    r"for guidance about", re.I)
# Unpublished films (home movies, amateur footage) are not public domain by publication date.
_LOC_UNPUBLISHED = re.compile(r"home movies?|amateur (?:film|footage|motion picture)|unpublished", re.I)


def _loc_item_restriction(rights_text):
    """The sentence that restricts this item, or "" when only general cautions are present."""
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", str(rights_text or "")):
        if not sentence.strip() or _LOC_GENERAL_SENTENCE.search(sentence):
            continue
        if _LOC_RESTRICTIVE.search(sentence):
            return sentence.strip()[:160]
    return ""


def _loc_rights_basis(hit):
    """Item-level reason, from the catalogue record, that the film is free to reuse in the US."""
    from core.footage_licence import us_public_domain_cutoff_year
    record = hit.get("item") if isinstance(hit.get("item"), dict) else {}
    published = _plain(record.get("created_published") or hit.get("created_published"), 300)
    described = _plain([hit.get("title"), record.get("genre"), record.get("notes"), record.get("medium")], 4000)
    if _LOC_UNPUBLISHED.search(described):
        return ""
    years = [int(year) for year in re.findall(r"\b(1[89]\d\d|20\d\d)\b", f"{hit.get('date') or ''} {published}")]
    if not years:
        return ""
    cutoff = us_public_domain_cutoff_year()
    year = max(years)
    if year < cutoff:
        where = "in the United States " if re.search(r"united states|u\.s\.", published, re.I) else ""
        return (f"catalogue record dates the film to {year}; works published {where}before {cutoff} "
                "are in the United States public domain")
    return ""


def _loc(query):
    """Library of Congress moving images.

    Search hits never carry a rights statement, so the item record is fetched for
    the most relevant hits. The Library's rights text is a collection-level
    assessment, not a licence; a film is offered only when the catalogue also
    dates it before the United States public-domain cutoff.
    """
    results = []
    for page in range(1, _search_pages() + 1):
        data = _get_json("https://www.loc.gov/film-and-videos/",
                         {"q": query, "fo": "json", "c": 25, "sp": page, "at": "results,pagination"})
        hits = (data or {}).get("results") or []
        _count("results_found", len(hits))
        leads = []
        for hit in hits:
            if hit.get("access_restricted"):
                continue
            media = []
            for resource in hit.get("resources") or []:
                if not isinstance(resource, dict):
                    continue
                direct = _secure_url(resource.get("video"))
                if direct and _has_video_suffix(direct, (".mp4", ".mov", ".m4v", ".webm")):
                    media.append(direct)
                stream = _secure_url(resource.get("video_stream"))
                if stream and ".m3u8" in stream:
                    media.append(stream)
                files = resource.get("files")
                for group in files if isinstance(files, list) else []:
                    for entry in (group if isinstance(group, list) else [group]):
                        candidate = _secure_url((entry or {}).get("url")) if isinstance(entry, dict) else ""
                        if candidate and _has_video_suffix(candidate, (".mp4", ".mov", ".m4v", ".webm")):
                            media.append(candidate)
            media = list(dict.fromkeys(media))
            if not media:
                continue
            # Direct files before HLS streams.
            media.sort(key=lambda link: ".m3u8" in link)
            # A film without a publication date before the public-domain cutoff can never be
            # selected, so no item request is spent on it (the Library limits requests strictly).
            if not _loc_rights_basis(hit):
                _reject("no publication date before the US public-domain cutoff in the catalogue record")
                continue
            leads.append((_lead_relevance(query, hit.get("title"), hit.get("description"), hit.get("subject")),
                          hit, media))
        leads.sort(key=lambda lead: -lead[0])
        for _score, hit, media in leads[:_detail_limit()]:
            page_url = _secure_url(hit.get("url") or hit.get("id"))
            if not page_url:
                continue
            rights_text = _plain(hit.get("rights") or hit.get("rights_advisory"), 3000)
            if not rights_text:
                detail = _get_json(page_url, {"fo": "json", "at": "item.rights,item.rights_advisory,"
                                                                  "item.rights_information,item.access_advisory"})
                if detail is None:
                    # The item record could not be read (rate limit, block or network). That is
                    # not a licence refusal; say so, and stop asking a host that was switched off.
                    _reject("item rights record could not be fetched", licence=False)
                    if _host_of(page_url) in _DISABLED_PROVIDER_HOSTS:
                        break
                    continue
                rights_text = _plain([(detail or {}).get(key) for key in (
                    "item.rights", "item.rights_advisory", "item.rights_information", "item.access_advisory")], 3000)
                if not rights_text and isinstance((detail or {}).get("item"), dict):
                    rights_text = _plain([detail["item"].get(key) for key in (
                        "rights", "rights_advisory", "rights_information", "access_advisory")], 3000)
            if not rights_text or not _LOC_NO_KNOWN.search(rights_text):
                _reject("item record has no 'no known restrictions' statement")
                continue
            restriction = _loc_item_restriction(rights_text)
            if restriction:
                _reject("item record states a restriction")
                continue
            resource = next((item for item in hit.get("resources") or [] if isinstance(item, dict)), {})
            contributors = hit.get("contributor") or []
            metadata = {
                "provider": "Library of Congress", "asset_id": page_url.rstrip("/").split("/")[-1],
                "title": _plain(hit.get("title"), 300), "description": _plain(hit.get("description")),
                "tags": [str(word) for word in (hit.get("subject") or [])][:20],
                "creator": _plain(", ".join(str(name) for name in contributors[:3]), 200),
                "date_created": str(hit.get("date") or "")[:20],
                "page_url": page_url, "licence": "No known copyright restrictions",
                "licence_url": page_url, "rights_statement": rights_text[:3000],
                "rights_basis": _loc_rights_basis(hit), "query": query,
                "credit": "Library of Congress, Motion Picture, Broadcasting, and Recorded Sound Division",
                "declared_width": int(resource.get("width") or 0), "declared_height": int(resource.get("height") or 0),
                "declared_duration_seconds": float(resource.get("duration") or 0),
                "alternate_urls": media[1:3],
                "licence_status": "Library reports no known restrictions for this collection; rights assessment remains the user's responsibility",
            }
            if _accept(metadata):
                results.append((media[0], metadata))
        if not ((data or {}).get("pagination") or {}).get("next"):
            break
    return results


def _nara(query):
    """Search NARA Catalog v2 and accept only item-level explicit reuse rights."""
    from core.config import NARA_API_KEY
    if not NARA_API_KEY:
        return []
    data = _get_json(
        "https://catalog.archives.gov/api/v2/records/search",
        # Parameter names from the published OpenAPI description (swagger.json): the page size is
        # "limit". The earlier "rows" is not a documented parameter.
        {"q": query, "availableOnline": "true", "typeOfMaterials": "Moving Images", "limit": 20},
        {"x-api-key": NARA_API_KEY, "Content-Type": "application/json"},
    )
    hits = (((data or {}).get("body") or {}).get("hits") or {}).get("hits") or []
    _count("results_found", len(hits))
    results = []
    for hit in hits:
        record = (((hit.get("_source") or {}).get("record")) or {})
        restriction = (record.get("useRestriction") or {}).get("status", "")
        notes = " ".join(str(record.get(k) or "") for k in
                         ("copyrightNote", "scopeAndContentNote", "generalNote", "title"))
        # An unrestricted access flag is NOT a copyright licence. Require an
        # explicit public-domain / US-government-work statement in item metadata.
        if str(restriction).lower() not in {"unrestricted", "unrestricted use"}:
            _reject(f"use restriction is {str(restriction or 'not stated')[:40]!r}, not 'Unrestricted'")
            continue
        if not re.search(r"public domain|work of the u\.s\. federal government|\bu\.s\. government work\b", notes, re.I):
            _reject("use is Unrestricted but the record has no public-domain or US-government-work statement")
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
            metadata = {
                "provider": "National Archives (NARA)", "asset_id": str(naid),
                "title": record.get("title"), "description": _plain(record.get("scopeAndContentNote")),
                "creator": "U.S. National Archives", "page_url": page,
                "licence": "Public Domain", "licence_url": page,
                "rights_statement": notes[:3000], "access_restriction": (record.get("accessRestriction") or {}).get("status"),
                "use_restriction": restriction, "query": query,
                "date_accessed": date.today().isoformat(),
                "attribution_text": f"{record.get('title') or 'Untitled'} — U.S. National Archives, NAID {naid}; {page}",
                "licence_status": "Explicit public-domain statement in NARA item metadata; verify record notes and third-party content",
            }
            if _accept(metadata):
                results.append((obj_url, metadata))
    return results


def _dvids_key():
    try:
        from core.config import DVIDS_API_KEY
        return (os.getenv("DVIDS_API_KEY", "") or DVIDS_API_KEY or "").strip()
    except ImportError:
        return os.getenv("DVIDS_API_KEY", "").strip()


# The Referer the previous version of this adapter sent with every DVIDS request.
DVIDS_LEGACY_REFERER = "https://api.dvidshub.net"
_DVIDS_RUN = {"headers": None, "legacy_tried": False}


def _dvids_headers():
    """DVIDS binds some keys to a registered origin; send it only when the operator set one."""
    origin = os.getenv("DVIDS_API_REFERER", "").strip()
    if origin:
        return {"Referer": origin, "Origin": origin.rstrip("/")}
    return _DVIDS_RUN["headers"]


def _dvids_get(url, params):
    """DVIDS request. A 403 without a Referer is retried once with the earlier fixed Referer.

    DVIDS issues two kinds of key: one that must be sent with no Referer and one bound to a
    registered origin. Which kind the operator holds is not knowable from the key, so the
    run finds out once and the report states which form the key accepted.
    """
    host = "api.dvidshub.net"
    headers = _dvids_headers()
    data = _get_json(url, params, headers)
    refused = _HOST_DISABLED_REASON.get(host, "").startswith("HTTP 403")
    if data is not None or headers is not None or _DVIDS_RUN["legacy_tried"] or not refused:
        return data
    _DVIDS_RUN["legacy_tried"] = True
    with _STATE_LOCK:
        _DISABLED_PROVIDER_HOSTS.discard(host)
        first_reason = _HOST_DISABLED_REASON.pop(host, "")
    legacy = {"Referer": DVIDS_LEGACY_REFERER}
    data = _get_json(url, params, legacy)
    name, report = active_scope()
    if data is None:
        note = (f"{first_reason} The request was refused both without a Referer and with the earlier "
                f"fixed Referer {DVIDS_LEGACY_REFERER}, so the key itself is not accepted.")
        _disable_host(host, note)
        if report is not None:
            report.fail(name or "DVIDS", note, search_status="auth_rejected")
        return None
    _DVIDS_RUN["headers"] = legacy
    note = (f"DVIDS refused the key without a Referer and accepted it with Referer {DVIDS_LEGACY_REFERER}. "
            "Set DVIDS_API_REFERER to the origin registered for the key to make this explicit.")
    print(f"  {note}")
    if report is not None:
        report.auth_recovered(name or "DVIDS", note)
    return data


def _dvids_files(asset, limit_bytes):
    """Rank an asset's renditions: largest MP4 at or below 1080p that fits the size limit."""
    ranked = []
    for entry in asset.get("files") or []:
        if not isinstance(entry, dict):
            continue
        src = _secure_url(entry.get("src"))
        kind = str(entry.get("type") or "").lower()
        if not src or not ("mp4" in kind or _has_video_suffix(src, (".mp4", ".m4v", ".mov"))):
            continue
        try:
            size, height, width = int(entry.get("size") or 0), int(entry.get("height") or 0), int(entry.get("width") or 0)
        except (TypeError, ValueError):
            size = height = width = 0
        if size and size > limit_bytes:
            continue
        ranked.append((height > 1080, -height, -size, src, width, height, size))
    ranked.sort()
    return [(src, width, height, size) for _over, _h, _s, src, width, height, size in ranked]


def _dvids(query):
    """DVIDS (U.S. Department of Defense media).

    ``/search`` returns no media links; each hit needs ``/asset`` for its files.
    DVIDS API Terms of Service I.B allow commercial use of assets served by the API.
    """
    api_key = _dvids_key()
    if not api_key:
        return []
    limit_bytes = clip_size_limit_mb() * 1024 * 1024
    results = []
    for page in range(1, _search_pages() + 1):
        data = _dvids_get("https://api.dvidshub.net/search", {
            "api_key": api_key, "q": query, "type": "video", "max_results": 15, "page": page,
        })
        hits = [hit for hit in (data or {}).get("results") or []
                if str(hit.get("type") or "").lower() == "video" or str(hit.get("id") or "").startswith("video:")]
        _count("results_found", len(hits))
        hits.sort(key=lambda hit: -_lead_relevance(
            query, hit.get("title"), hit.get("short_description"), hit.get("keywords")))
        for hit in hits[:_detail_limit()]:
            asset_id = str(hit.get("id") or "").strip()
            page_url = _secure_url(hit.get("url"))
            if not asset_id or not page_url:
                continue
            detail = _dvids_get("https://api.dvidshub.net/asset", {"id": asset_id, "api_key": api_key})
            asset = (detail or {}).get("results") or {}
            if isinstance(asset, list):
                asset = asset[0] if asset else {}
            choices = _dvids_files(asset, limit_bytes)
            urls = [src for src, _w, _h, _s in choices]
            if not urls:
                hls = _secure_url(asset.get("hls_url") or hit.get("hls_url"))
                if not hls:
                    continue
                urls, choices = [hls], [(hls, 0, 0, 0)]
            credit = asset.get("credit") or hit.get("credit") or ""
            if isinstance(credit, list):
                credit = ", ".join(str((entry or {}).get("name") if isinstance(entry, dict) else entry)
                                   for entry in credit if entry)
            unit = _plain(asset.get("unit_name") or hit.get("unit_name"), 160)
            creator = _plain(credit, 200) or unit
            title = _plain(asset.get("title") or hit.get("title"), 300)
            metadata = {
                "provider": "DVIDS", "asset_id": asset_id, "title": title,
                "description": _plain(asset.get("description") or hit.get("short_description")),
                "tags": [word.strip() for word in str(asset.get("keywords") or hit.get("keywords") or "").split(",") if word.strip()][:20],
                "creator": creator, "unit": unit, "branch": _plain(asset.get("branch") or hit.get("branch"), 80),
                "virin": _plain(asset.get("virin"), 60),
                "date_created": str(asset.get("date") or hit.get("date") or "")[:25],
                "page_url": page_url,
                "licence": "DVIDS API Commercial Use Terms", "licence_url": DVIDS_TERMS_URL,
                "query": query, "declared_width": choices[0][1], "declared_height": choices[0][2],
                "declared_size_bytes": choices[0][3],
                "declared_duration_seconds": float(asset.get("duration") or hit.get("duration") or 0),
                "alternate_urls": urls[1:3],
                "attribution_text": f"{title or 'Untitled'}, {creator or 'U.S. Department of Defense'}, via DVIDS; {page_url}",
                "rights_statement": "DVIDS API Terms of Service I.B: assets accessible via the API are free for commercial use; attribution to DVIDS is required; cached assets must be removed when DVIDS removes them.",
                "licence_status": "DVIDS item returned through the official API; DoD visual-information disclaimer and identifiable people still need human review",
            }
            if _accept(metadata):
                results.append((urls[0], metadata))
        if len(hits) < 15:
            break
    return results


def _europeana_cc(query):
    """Europeana video records that are open for reuse and expose a direct media file."""
    from core.config import EUROPEANA_API_KEY
    if not EUROPEANA_API_KEY:
        return []
    from core.footage_licence import parse_creative_commons
    results, cursor = [], "*"
    for _page in range(_search_pages()):
        data = _get_json("https://api.europeana.eu/record/v2/search.json", {
            "query": query, "qf": "TYPE:VIDEO", "reusability": "open", "media": "true",
            "rows": 20, "profile": "standard", "cursor": cursor,
        }, {"X-Api-Key": EUROPEANA_API_KEY})
        items = (data or {}).get("items") or []
        _count("results_found", len(items))
        for item in items:
            rights = str(_first(item.get("rights")) or "").strip()
            media_url = next((link for link in (_secure_url(value) for value in (item.get("edmIsShownBy") or []))
                              if link and _has_video_suffix(link)), "")
            if not media_url:
                continue
            record_id = str(item.get("id") or "").strip()
            page_url = (f"https://www.europeana.eu/item{record_id}" if record_id.startswith("/")
                        else _canonical_media_url(_secure_url(item.get("guid"))))
            parsed = parse_creative_commons(rights) if rights else None
            english = ((item.get("dcTitleLangAware") or {}).get("en") or [])
            descriptions = ((item.get("dcDescriptionLangAware") or {}).get("en") or item.get("dcDescription") or [])
            metadata = {
                "provider": "Europeana", "asset_id": record_id or page_url,
                "title": _plain(_first(english) or _first(item.get("title")), 300),
                "description": _plain(descriptions), "tags": [str(name) for name in (item.get("title") or [])][:6],
                "creator": _plain(_first(item.get("dcCreator")) or _first(item.get("dataProvider")), 200),
                "holding_institution": _plain(_first(item.get("dataProvider")), 200),
                "date_created": str(_first(item.get("year")) or "")[:10],
                "page_url": page_url, "source_page_url": _secure_url(_first(item.get("edmIsShownAt"))),
                "licence": (parsed or {}).get("label", ""), "licence_url": rights, "query": query,
            }
            if _accept(metadata):
                results.append((media_url, metadata))
        cursor = (data or {}).get("nextCursor")
        if not cursor or len(items) < 20:
            break
    return results


MIXKIT_LICENCE_URL = "https://mixkit.co/license/"
MIXKIT_DIR = Path("assets/clips/mixkit")
LOCAL_LIBRARY_DIRS = (Path("assets/clips/archival_trial"), Path("assets/clips/indian_archives"), MIXKIT_DIR)
LOCAL_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


def register_mixkit_asset(source_file, *, title, source_url, licence, creator="", attribution="",
                          description="", tags=None, validate=True):
    """Register a user-selected Mixkit Free License download; never scrapes Mixkit.

    Mixkit has no public API and its terms forbid automated downloading, so a
    person downloads the clip and confirms the licence shown on the item page.
    The file itself is checked with ffprobe before it enters the library.
    """
    from shutil import copy2
    from datetime import date as _date
    source = Path(source_file)
    if not source.is_file():
        raise FileNotFoundError(source)
    if str(licence).strip().lower() != "mixkit free license":
        raise ValueError("Only an explicitly verified 'Mixkit Free License' asset can be registered; Restricted License is not accepted.")
    if not re.match(r"^https://(?:www\.)?mixkit\.co/free-stock-video/[^/?#]+", str(source_url).strip()):
        raise ValueError("source_url must be the exact Mixkit asset page URL "
                         "(https://mixkit.co/free-stock-video/<clip-name>/).")
    if not str(title).strip():
        raise ValueError("The exact Mixkit clip title is required.")
    facts = {}
    if validate:
        from core.footage_validation import validate_media
        facts = validate_media(source)
        if not facts["ok"]:
            raise ValueError(f"{source.name} is not usable footage: {facts['status']}: {facts['reason']}")
    target_dir = MIXKIT_DIR.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source.resolve() != target.resolve():
        copy2(source, target)
    metadata = {
        "provider": "Mixkit (manual user-selected import)",
        "asset_id": source.stem,
        "title": title.strip(),
        "description": str(description or "").strip(),
        "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
        "creator": creator.strip(),
        "page_url": str(source_url).strip(),
        "licence": "Mixkit Free License",
        "licence_url": MIXKIT_LICENCE_URL,
        "attribution_text": attribution.strip() or f"{title.strip()} — Mixkit; {source_url}",
        "date_accessed": _date.today().isoformat(),
        "terms_reviewed": True,
        "review_basis": "User confirmed this exact asset is labelled Free License on its Mixkit page.",
        "automated_download": False,
        "media_type": "video",
        "human_visual_inspection_required": True,
        "visual_checks": {"steady_clean": "not_automatically_verified",
                          "watermark_logo": "not_automatically_verified",
                          "private_person_privacy": "not_automatically_verified"},
    }
    if facts:
        metadata.update({
            "width": facts["width"], "height": facts["height"],
            "duration_seconds": facts["duration_seconds"], "file_size_bytes": facts["size_bytes"],
            "media_sha256": facts.get("sha256", ""), "container": facts["container"],
            "video_codec": facts["video_codec"],
            "validation": {"ok": True, "status": "valid", "reason": facts["reason"], "tool": "ffprobe"},
        })
    sidecar = target.with_suffix(target.suffix + ".source.json")
    sidecar.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, sidecar


def import_mixkit_manifest(manifest_path, *, confirm_free_license=False):
    """Import several manually downloaded Mixkit clips described by one manifest.

    The manifest is a JSON list (or ``{"assets": [...]}``) or a CSV file with the
    columns ``file, title, source_url, licence, creator, attribution, description,
    tags``. File paths are relative to the manifest. Every entry is validated on
    its own; one bad entry does not stop the rest. Nothing is downloaded.
    """
    manifest = Path(manifest_path)
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    if manifest.suffix.lower() == ".csv":
        import csv
        with manifest.open(newline="", encoding="utf-8-sig") as handle:
            entries = [dict(row) for row in csv.DictReader(handle)]
    else:
        loaded = json.loads(manifest.read_text(encoding="utf-8-sig"))
        entries = loaded.get("assets") if isinstance(loaded, dict) else loaded
    if not isinstance(entries, list):
        raise ValueError("The manifest must be a list of assets.")
    imported, rejected = [], []
    for number, entry in enumerate(entries, start=1):
        entry = entry if isinstance(entry, dict) else {}
        name = str(entry.get("file") or "").strip()
        try:
            if not confirm_free_license:
                raise ValueError("the operator has not confirmed the Free License label for these clips")
            if not name:
                raise ValueError("no file given")
            source = Path(name)
            if not source.is_absolute():
                source = manifest.parent / source
            tags = entry.get("tags") or []
            if isinstance(tags, str):
                tags = [tag for tag in re.split(r"[;,|]", tags) if tag.strip()]
            target, sidecar = register_mixkit_asset(
                source, title=str(entry.get("title") or ""), source_url=str(entry.get("source_url") or ""),
                licence=str(entry.get("licence") or entry.get("license") or ""),
                creator=str(entry.get("creator") or ""), attribution=str(entry.get("attribution") or ""),
                description=str(entry.get("description") or ""), tags=tags)
            imported.append({"entry": number, "file": name, "stored_as": str(target), "sidecar": str(sidecar)})
        except (OSError, ValueError) as exc:
            rejected.append({"entry": number, "file": name, "reason": str(exc)})
    return {"manifest": str(manifest), "entries": len(entries), "imported": imported, "rejected": rejected}


def _tokens(value):
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "about",            "scene", "footage", "video", "documentary", "file"}
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(word) > 2 and word not in stop}


def _metadata_relevance(query, metadata):
    wanted = _tokens(query)
    found = (_tokens(metadata.get("title")) | _tokens(metadata.get("description"))
             | _tokens(metadata.get("tags")) | _tokens(metadata.get("creator")))
    return len(wanted & found) / len(wanted) if wanted else 0.0


# Words the query expansion adds ("... newsreel", "... historical film"). They describe the kind of
# footage wanted, not its subject, so a match on these alone says nothing about the scene.
_GENERIC_QUERY_WORDS = frozenset({
    "archival", "archive", "archives", "newsreel", "newsreels", "historical", "historic", "history",
    "film", "films", "period", "old", "vintage", "genuine", "authentic", "real", "stock", "clip", "clips",
})


def _subject_relevance(query, metadata):
    """Relevance of a result to a query, zero unless it shares a subject word with the query.

    "night traffic newsreel" must not accept "Harbour ships newsreel" because both say "newsreel".
    """
    score = _metadata_relevance(query, metadata)
    subject = _tokens(query) - _GENERIC_QUERY_WORDS
    if not subject:
        return score
    found = (_tokens(metadata.get("title")) | _tokens(metadata.get("description"))
             | _tokens(metadata.get("tags")) | _tokens(metadata.get("creator")))
    return score if subject & found else 0.0


def minimum_relevance():
    """Lowest text relevance accepted for an archive result: ``FOOTAGE_MIN_RELEVANCE`` (0.25)."""
    return min(1.0, max(0.0, _env_number("FOOTAGE_MIN_RELEVANCE", 0.25)))


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


_NASA_TOPICS = {
    "space", "nasa", "planet", "mars", "moon", "lunar", "astronaut", "rocket", "apollo", "saturn",
    "satellite", "orbit", "shuttle", "telescope", "galaxy", "solar", "spacecraft", "launch", "iss",
    "hubble", "comet", "asteroid", "jupiter", "venus", "mercury", "cosmos", "universe",
}


def _host_status(host, enabled=True, auth="not_required", reason=""):
    if host in _DISABLED_PROVIDER_HOSTS:
        why = _HOST_DISABLED_REASON.get(host, "switched off earlier in this run")
        return False, ("rejected" if why.startswith(("HTTP 401", "HTTP 403")) else auth), why
    return enabled, auth, reason


def _key_status(host, value, env_name):
    if not str(value or "").strip():
        return False, "missing_key", f"{env_name} is not set"
    return _host_status(host, True, "configured")


def archive_providers(context=""):
    """The archive sources behind the common interface.

    Built on every call, and each ``search`` looks its adapter up by name, so a
    replaced adapter (tests, a local override) is the one that runs.
    """
    from core.footage_engine import Provider

    def nara_status():
        from core.config import NARA_API_KEY
        return _key_status("catalog.archives.gov", NARA_API_KEY, "NARA_API_KEY")

    def europeana_status():
        from core.config import EUROPEANA_API_KEY
        return _key_status("api.europeana.eu", EUROPEANA_API_KEY, "EUROPEANA_API_KEY")

    providers = []
    if _tokens(context) & _NASA_TOPICS:
        providers.append(Provider("NASA", lambda query: _nasa(query),
                                  status=lambda: _host_status("images-api.nasa.gov")))
    providers += [
        Provider("National Archives (NARA)", lambda query: _nara(query), status=nara_status),
        Provider("DVIDS", lambda query: _dvids(query),
                 status=lambda: _key_status("api.dvidshub.net", _dvids_key(), "DVIDS_API_KEY")),
        Provider("Library of Congress", lambda query: _loc(query), status=lambda: _host_status("www.loc.gov")),
        Provider("Wikimedia Commons", lambda query: _wikimedia(query),
                 status=lambda: _host_status("commons.wikimedia.org")),
        Provider("Internet Archive", lambda query: _internet_archive(query),
                 status=lambda: _host_status("archive.org")),
        Provider("Europeana", lambda query: _europeana_cc(query), status=europeana_status),
    ]
    return providers


def _mark_archival(metadata):
    """Record what a reviewer must verify for a scene that asked for genuine archive film."""
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


def _local_library_clip(keywords, path, used_ids, scene_prompt, report, label):
    """Prefer operator-imported clips (Indian archives, Mixkit) whose sidecar proves the licence."""
    from core.footage_engine import HASH_PREFIX
    from core.footage_licence import classify_licence
    local_query = " ".join(str(value) for value in (keywords or []) if str(value).strip()) or str(scene_prompt or "")
    name = "Local licensed library"
    for folder in LOCAL_LIBRARY_DIRS:
        if not folder.is_dir():
            continue
        for media_path in sorted(folder.iterdir()):
            if not media_path.is_file() or media_path.suffix.lower() not in LOCAL_VIDEO_SUFFIXES:
                continue
            sidecar = media_path.with_suffix(media_path.suffix + ".source.json")
            if not sidecar.is_file():
                continue
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            report.add(name, "results_found")
            decision = classify_licence(metadata)
            if not decision["publishable"]:
                report.add(name, "licence_rejections")
                continue
            identity = f"{metadata.get('provider')}:{metadata.get('asset_id') or media_path.name}"
            if identity in used_ids:
                continue
            score = _metadata_relevance(local_query, metadata)
            if score < 0.25:
                continue
            report.add(name, "download_attempts")
            if not _valid_video(media_path):
                report.add(name, "invalid_media")
                report.fail(name, f"{media_path.name} failed ffprobe validation")
                continue
            digest = str(metadata.get("media_sha256") or "")
            if not digest:
                try:
                    from core.footage_validation import sha256_of
                    digest = sha256_of(media_path)
                except OSError:
                    digest = ""
            if digest and HASH_PREFIX + digest in used_ids:
                continue
            import shutil
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(media_path, target)
            metadata.update({"relevance_score": round(score, 3), "licence_state": decision["state"],
                             "licence_reason": decision["reason"], "local_library_path": str(media_path)})
            if digest:
                metadata["media_sha256"] = digest
            target.with_suffix(target.suffix + ".source.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            used_ids.add(identity)
            if digest:
                used_ids.add(HASH_PREFIX + digest)
            report.set(name, enabled=True, auth_status="not_required", search_status="ok")
            for counter in ("download_successes", "eligible_assets", "selected_scenes"):
                report.add(name, counter)
            report.scene({
                "scene": label, "status": "selected", "queries": [local_query],
                "reason": "operator-imported licensed clip matched the scene",
                "selected": {"provider": metadata.get("provider"), "asset_id": metadata.get("asset_id"),
                             "title": metadata.get("title"), "page_url": metadata.get("page_url"),
                             "licence": metadata.get("licence"), "licence_state": decision["state"],
                             "relevance_score": round(score, 3), "width": metadata.get("width"),
                             "height": metadata.get("height"), "media_sha256": digest, "local_path": str(target)},
            })
            print(f"  clip ok (local licensed import: {metadata.get('provider')}); human review required")
            return True
    return False


def fetch_documentary_clip(keywords, path, used_ids=None, scene_prompt="", *,
                           extra_providers=None, scene_label=""):
    """Find one licensed, validated clip for a scene across every configured source.

    Operator-imported clips are tried first. Then all providers are searched
    together and ranked as one pool by ``core.footage_engine``. Text matching is
    a discovery filter, not computer-vision verification: every selected clip
    still needs human review. ``extra_providers`` adds stock libraries to the pool.
    """
    from core.footage_engine import acquire
    from core.footage_report import current_report
    used_ids = used_ids if used_ids is not None else set()
    report = current_report()
    label = scene_label or Path(path).stem
    context = " ".join(str(x) for x in (keywords or [])) + " " + str(scene_prompt or "")
    archival_request = _is_archival_video_request(keywords, scene_prompt)
    if _local_library_clip(keywords, path, used_ids, scene_prompt, report, label):
        return True

    providers = archive_providers(context) + list(extra_providers or [])

    def prepare(metadata, _score, _query):
        metadata["date_accessed"] = metadata.get("date_accessed") or date.today().isoformat()
        if archival_request:
            # An archive candidate, not an automatic authenticity certification.
            _mark_archival(metadata)

    def relevance(query, metadata):
        return _subject_relevance(query, metadata)

    def download(url, target, metadata):
        return _download(url, target, metadata)

    return acquire(
        _expanded_queries(keywords, scene_prompt), path, used_ids, scene_prompt=scene_prompt,
        providers=providers, download=download, relevance=relevance, min_relevance=minimum_relevance(),
        report=report, scene_label=label, prepare=prepare,
    )


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
