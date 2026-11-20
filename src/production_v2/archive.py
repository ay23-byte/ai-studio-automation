"""Explicitly selected Wikimedia Commons assets; no automatic relevance claims.

Commons metadata is not a legal guarantee. Operators must review the source page,
relevance, depicted rights and license before use. No arbitrary URLs are fetched.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .artifacts import sha256_file
from .licenses import AssetRecord, validate_asset

API = "https://commons.wikimedia.org/w/api.php"
MAX_BYTES = 100 * 1024 * 1024
LICENSES = {
    "https://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
    "https://creativecommons.org/licenses/by/3.0/": "CC BY 3.0",
    "https://creativecommons.org/licenses/by-sa/4.0/": "CC BY-SA 4.0",
    "https://creativecommons.org/licenses/by-sa/3.0/": "CC BY-SA 3.0",
    "https://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
}
MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "video/webm": ".webm", "video/mp4": ".mp4", "video/ogg": ".ogv"}


def _plain(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", value or "")).strip()


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_host(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"commons.wikimedia.org", "upload.wikimedia.org"} or p.username or p.password or p.port not in (None, 443):
        raise ValueError("archive URL must be HTTPS on a Wikimedia Commons host")


def _read(url: str, limit: int, timeout: float) -> bytes:
    _check_host(url)
    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "EngineeringPhysicsStudio/2.0 (Wikimedia Commons asset selection)"})
    with opener.open(req, timeout=timeout) as response:
        _check_host(response.geturl())
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("archive response exceeds size limit")
    return data


def download_commons(title: str, destination: Path, scene_id: str, *, timeout: float = 20, fetch=_read) -> tuple[Path, AssetRecord]:
    """Retrieve one explicitly reviewed File: title with strict license allowlist."""
    if not title.startswith("File:") or len(title) > 240 or "\n" in title:
        raise ValueError("Commons selection must be a File: title")
    query = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|mime|size|extmetadata", "titles": title})
    payload = json.loads(fetch(API + "?" + query, 2 * 1024 * 1024, timeout))
    pages = list(payload["query"]["pages"].values())
    if len(pages) != 1 or "imageinfo" not in pages[0]:
        raise ValueError("Commons file not found")
    page = pages[0]
    info = page["imageinfo"][0]
    meta = info.get("extmetadata") or {}
    get = lambda key: _plain((meta.get(key) or {}).get("value", ""))
    license_url = get("LicenseUrl").replace("http://creativecommons.org/", "https://creativecommons.org/").rstrip("/") + "/"
    license_name = LICENSES.get(license_url)
    creator = get("Artist")
    if not license_name or not creator:
        raise ValueError("Commons asset requires a supported exact license URL and creator")
    mime = info.get("mime", "")
    if mime not in MIMES:
        raise ValueError("unsupported Commons media type")
    if int(info.get("size") or 0) > MAX_BYTES:
        raise ValueError("archive file exceeds size limit")
    source_url = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(page.get("title", title).replace(" ", "_"), safe=":")
    url = info["url"]
    _check_host(url)
    data = fetch(url, MAX_BYTES, timeout)
    if not data:
        raise ValueError("archive file is empty")
    destination = Path(destination).with_suffix(MIMES[mime])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    record = AssetRecord(id="visual_" + scene_id, source_url=source_url, creator=creator,
        license=license_name, attribution_text=f"{page.get('title', title)} by {creator}; {license_name} ({license_url}); {source_url}",
        downloaded_at=dt.datetime.now(dt.timezone.utc).isoformat(), sha256=sha256_file(destination),
        media_type="video" if mime.startswith("video/") else "image", resolution=f"{info.get('width', '')}x{info.get('height', '')}",
        restrictions="Review source page, depicted rights, attribution and relevance before use.", used_in=scene_id)
    if not validate_asset(record).eligible:
        destination.unlink(missing_ok=True)
        raise ValueError("Commons rights record is incomplete")
    return destination, record
