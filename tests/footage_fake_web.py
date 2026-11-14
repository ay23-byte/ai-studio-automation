"""An offline stand-in for the footage providers' web APIs.

``FakeWeb.get`` replaces ``requests.get``. It answers the same URLs the adapters
call, in the response shapes the live APIs returned on 2026-11-14 (field names
and nesting were copied from real responses), and serves real media files made
with FFmpeg. It proves the code handles those shapes and those files. It does not
prove that any provider is reachable or that a key works.
"""
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import requests

from tests.footage_fixture import make_clip, truncated_copy


class FakeResponse:
    def __init__(self, status=200, *, payload=None, body=b"", headers=None, content_type=None):
        self.status_code = status
        self._payload = payload
        self._body = body if payload is None else json.dumps(payload).encode("utf-8")
        self.headers = dict(headers or {})
        if content_type or payload is not None:
            self.headers.setdefault("content-type", content_type or "application/json; charset=utf-8")
        if payload is None and body:
            self.headers.setdefault("Content-Length", str(len(body)))

    @property
    def text(self):
        return self._body.decode("utf-8", "replace")

    @property
    def content(self):
        return self._body

    def json(self):
        if self._payload is None:
            return json.loads(self._body.decode("utf-8"))
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeWeb:
    """Routes provider URLs to canned answers. ``failures`` maps a host to a status code or exception."""

    def __init__(self, root):
        self.root = Path(root)
        self.media = {}          # url without query -> bytes
        self.calls = []          # (host, path, params, headers)
        self.failures = {}       # host -> int status | Exception | callable(url) -> FakeResponse
        self.failure_headers = {}
        self.catalogue = {key: [] for key in (
            "nasa", "wikimedia", "internet_archive", "loc", "dvids", "europeana", "pixabay", "coverr")}
        self._clip_number = 0

    # -- media ---------------------------------------------------------------
    def clip(self, url, *, size="640x360", seconds=4, pattern=None, kind="valid"):
        """Publish a media file at ``url``. ``kind``: valid | html | truncated | tiny | audio."""
        self._clip_number += 1
        path = self.root / f"media_{self._clip_number}.mp4"
        if kind == "html":
            data = b"<!DOCTYPE html><html><body>" + b"Access denied. " * 6000 + b"</body></html>"
        else:
            # A different tag per clip gives every file different bytes.
            make_clip(path, size=size, seconds=seconds, pattern=pattern or "testsrc2",
                      tag=f"clip-{self._clip_number}")
            if kind == "truncated":
                truncated_copy(path, path.with_name("cut_" + path.name))
                data = path.with_name("cut_" + path.name).read_bytes()
            else:
                data = path.read_bytes()
        self.media[url.split("?")[0]] = data
        return url

    def same_file(self, url, other_url):
        """Publish the bytes of ``url`` at ``other_url`` as well (one film, two catalogue entries)."""
        self.media[other_url.split("?")[0]] = self.media[url.split("?")[0]]
        return other_url

    # -- catalogue entries -----------------------------------------------------
    def add_nasa(self, nasa_id, title, description, *, center="KSC", keywords=(), **clip):
        base = f"images-assets.nasa.gov/video/{nasa_id}/{nasa_id}"
        self.clip(f"https://{base}~orig.mp4", **clip)
        self.catalogue["nasa"].append({
            "href": f"https://images-assets.nasa.gov/video/{nasa_id}/collection.json",
            "data": [{"nasa_id": nasa_id, "title": title, "description": description, "center": center,
                      "keywords": list(keywords), "media_type": "video", "date_created": "1969-07-16T00:00:00Z"}],
            "links": [{"rel": "preview", "href": f"https://{base}~thumb.jpg", "render": "image"}],
        })

    def add_wikimedia(self, page_id, name, description, *, licence="CC BY-SA 4.0",
                      licence_url="https://creativecommons.org/licenses/by-sa/4.0", artist="Commons User",
                      width=768, height=576, **clip):
        file_name = name.replace(" ", "_") + ".webm"
        url = f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{file_name}"
        self.clip(url, **clip)
        self.catalogue["wikimedia"].append({
            "pageid": page_id, "ns": 6, "title": f"File:{name}.webm", "index": len(self.catalogue["wikimedia"]) + 1,
            "videoinfo": [{
                "size": len(self.media[url]), "width": width, "height": height, "duration": 4.0,
                "url": url + "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{file_name}",
                "mime": "video/webm", "derivatives": [],
                "extmetadata": {key: {"value": value} for key, value in {
                    "ObjectName": name, "ImageDescription": description, "Artist": artist, "Credit": "Own work",
                    "LicenseShortName": licence, "LicenseUrl": licence_url, "AttributionRequired": "true",
                    "Copyrighted": "True", "Restrictions": "", "Categories": "Videos"}.items()},
            }],
        })

    def add_internet_archive(self, identifier, title, description, *,
                             licenseurl="http://creativecommons.org/licenses/publicdomain/", creator="Archive Maker",
                             **clip):
        name = f"{identifier}.mp4"
        url = f"https://archive.org/download/{identifier}/{name}"
        self.clip(url, **clip)
        self.catalogue["internet_archive"].append({
            "doc": {"identifier": identifier, "title": title, "description": description,
                    "licenseurl": licenseurl, "creator": creator, "year": 1950},
            "detail": {"metadata": {"identifier": identifier, "title": title, "description": description,
                                    "licenseurl": licenseurl, "creator": creator, "date": "1950"},
                       "files": [{"name": name, "source": "derivative", "format": "h.264",
                                  "size": str(len(self.media[url]))}]},
        })

    def add_loc(self, item_id, title, description, *, year="1905", rights=None, **clip):
        url = f"https://tile.loc.gov/storage-services/service/mbrs/ntscrm/{item_id}/{item_id}.mp4"
        self.clip(url, **clip)
        self.catalogue["loc"].append({
            "hit": {"id": f"http://www.loc.gov/item/{item_id}/", "url": f"https://www.loc.gov/item/{item_id}/",
                    "title": title, "description": [description], "date": year, "access_restricted": False,
                    "contributor": ["thomas a. edison, inc"], "subject": ["actualities"],
                    "item": {"created_published": [f"United States : Thomas A. Edison, Inc., {year}."]},
                    "resources": [{"files": 1, "video": url, "width": 640, "height": 360, "duration": 4,
                                   "url": f"https://www.loc.gov/resource/ntscrm.{item_id}/"}]},
            "rights": {"item.rights": [rights or (
                "The Library of Congress is not aware of any U.S. copyright or other restrictions in the vast "
                "majority of motion pictures in these collections. Rights assessment is your responsibility. "
                "Credit Line: Library of Congress, Motion Picture, Broadcasting, and Recorded Sound Division.")]},
        })

    def add_dvids(self, number, title, description, **clip):
        url = f"https://d34w7g4gy10iej.cloudfront.net/video/{number}/DOD_{number}-1280x720-3000k.mp4"
        self.clip(url, **clip)
        self.catalogue["dvids"].append({
            "hit": {"id": f"video:{number}", "type": "video", "title": title, "short_description": description,
                    "keywords": "", "credit": "Sgt. Example", "duration": 4,
                    "url": f"https://www.dvidshub.net/video/{number}/example"},
            "asset": {"id": f"video:{number}", "title": title, "description": description, "duration": 4,
                      "credit": [{"name": "Sgt. Example"}], "unit_name": "1st Brigade", "branch": "Army",
                      "url": f"https://www.dvidshub.net/video/{number}/example",
                      "files": [{"src": url, "type": "video/mp4", "width": 1280, "height": 720,
                                 "size": len(self.media[url]), "bitrate": 3000}]},
        })

    def add_europeana(self, record, title, description, *,
                      rights="http://creativecommons.org/publicdomain/mark/1.0/", **clip):
        url = f"https://www.openbeelden.nl/files/{record}.mp4"
        self.clip(url, **clip)
        self.catalogue["europeana"].append({
            "id": f"/2051906/{record}", "type": "VIDEO", "title": [title],
            "dcTitleLangAware": {"en": [title]}, "dcDescriptionLangAware": {"en": [description]},
            "dcDescription": [description], "dcCreator": ["Polygoon-Profilti (producer)"],
            "dataProvider": ["Netherlands Institute for Sound & Vision"], "rights": [rights],
            "edmIsShownBy": [url], "edmIsShownAt": [f"https://www.openbeelden.nl/media/{record}"],
            "guid": f"https://www.europeana.eu/item/2051906/{record}?utm_source=api",
        })

    def add_pixabay(self, number, tags, **clip):
        url = f"https://cdn.pixabay.com/video/{number}_medium.mp4"
        self.clip(url, **{"size": "1280x720", **clip})
        self.catalogue["pixabay"].append({
            "id": number, "pageURL": f"https://pixabay.com/videos/id-{number}/", "type": "film", "tags": tags,
            "duration": 12, "user": "PixabayUser",
            "videos": {"large": {"url": "", "width": 0, "height": 0, "size": 0},
                       "medium": {"url": url, "width": 1280, "height": 720, "size": len(self.media[url])}},
        })

    def add_coverr(self, video_id, title, description, *, tags=(), ai=False, **clip):
        url = f"https://cdn.coverr.co/videos/{video_id}/1080p.mp4"
        self.clip(url, **{"size": "1280x720", **clip})
        self.catalogue["coverr"].append({
            "id": video_id, "title": title, "description": description, "tags": list(tags), "duration": 9.5,
            "max_width": 1920, "max_height": 1080, "is_vertical": False, "is_ai_generated": ai,
            "urls": {"mp4": url, "mp4_download": url, "mp4_preview": url},
        })

    # -- the fake requests.get ----------------------------------------------------
    @staticmethod
    def _matches(query, *texts):
        words = [word for word in str(query or "").lower().replace('"', " ").split() if len(word) > 2]
        haystack = " ".join(str(text) for text in texts).lower()
        return bool(words) and any(word in haystack for word in words)

    def get(self, url, params=None, headers=None, timeout=None, stream=False, **_kwargs):
        if timeout is None:
            raise AssertionError(f"request without a timeout: {url}")
        parts = urlsplit(url)
        host, path = parts.netloc, parts.path
        query = dict(parse_qsl(parts.query))
        for key, value in (params or {}).items():
            query[key] = value
        self.calls.append((host, path, dict(query), dict(headers or {})))
        failure = self.failures.get(host)
        if failure is not None:
            if isinstance(failure, Exception):
                raise failure
            if callable(failure):
                return failure(url)
            return FakeResponse(int(failure), body=b"refused", headers=self.failure_headers.get(host),
                                content_type="text/plain")
        base = f"https://{host}{path}"
        if base in self.media:
            return FakeResponse(200, body=self.media[base], content_type="video/mp4")

        if host == "images-api.nasa.gov":
            items = [item for item in self.catalogue["nasa"]
                     if self._matches(query.get("q"), item["data"][0]["title"], item["data"][0]["description"])]
            return FakeResponse(payload={"collection": {"items": items, "metadata": {"total_hits": len(items)}}})
        if host == "images-assets.nasa.gov" and path.endswith("/collection.json"):
            nasa_id = path.split("/")[2]
            # The live manifest lists http:// links.
            return FakeResponse(payload=[
                f"http://images-assets.nasa.gov/video/{nasa_id}/{nasa_id}~orig.mp4",
                f"http://images-assets.nasa.gov/video/{nasa_id}/{nasa_id}~thumb.jpg"])
        if host == "commons.wikimedia.org":
            wanted = str(query.get("gsrsearch") or "").replace("filetype:video", "")
            pages = {str(page["pageid"]): page for page in self.catalogue["wikimedia"]
                     if self._matches(wanted, page["title"],
                                      page["videoinfo"][0]["extmetadata"]["ImageDescription"]["value"])}
            return FakeResponse(payload={"batchcomplete": "", "query": {"pages": pages}} if pages
                                else {"batchcomplete": ""})
        if host == "archive.org" and path == "/advancedsearch.php":
            wanted = str(query.get("q") or "").split(") AND")[0].lstrip("(")
            docs = [entry["doc"] for entry in self.catalogue["internet_archive"]
                    if self._matches(wanted, entry["doc"]["title"], entry["doc"]["description"])]
            return FakeResponse(payload={"response": {"numFound": len(docs), "docs": docs}})
        if host == "archive.org" and path.startswith("/metadata/"):
            identifier = path.split("/")[2]
            entry = next((item for item in self.catalogue["internet_archive"]
                          if item["doc"]["identifier"] == identifier), None)
            return FakeResponse(payload=entry["detail"] if entry else {})
        if host == "www.loc.gov" and path == "/film-and-videos/":
            hits = [entry["hit"] for entry in self.catalogue["loc"]
                    if self._matches(query.get("q"), entry["hit"]["title"], entry["hit"]["description"])]
            return FakeResponse(payload={"results": hits, "pagination": {"next": None}})
        if host == "www.loc.gov" and path.startswith("/item/"):
            entry = next((item for item in self.catalogue["loc"] if item["hit"]["url"].endswith(path)), None)
            return FakeResponse(payload=entry["rights"] if entry else {})
        if host == "api.dvidshub.net" and path == "/search":
            hits = [entry["hit"] for entry in self.catalogue["dvids"]
                    if self._matches(query.get("q"), entry["hit"]["title"], entry["hit"]["short_description"])]
            return FakeResponse(payload={"results": hits, "page_info": {"total_results": len(hits)}})
        if host == "api.dvidshub.net" and path == "/asset":
            entry = next((item for item in self.catalogue["dvids"] if item["hit"]["id"] == query.get("id")), None)
            return FakeResponse(payload={"results": entry["asset"]} if entry else {"results": {}})
        if host == "api.europeana.eu":
            items = [item for item in self.catalogue["europeana"]
                     if self._matches(query.get("query"), item["title"], item["dcDescription"])]
            return FakeResponse(payload={"success": True, "itemsCount": len(items), "items": items})
        if host == "pixabay.com":
            hits = [hit for hit in self.catalogue["pixabay"] if self._matches(query.get("q"), hit["tags"])]
            return FakeResponse(payload={"total": len(hits), "totalHits": len(hits), "hits": hits},
                                headers={"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "99",
                                         "X-RateLimit-Reset": "60"})
        if host == "api.coverr.co":
            hits = [hit for hit in self.catalogue["coverr"]
                    if self._matches(query.get("query"), hit["title"], hit["description"], " ".join(hit["tags"]))]
            return FakeResponse(payload={"page": 0, "pages": 1, "page_size": 20, "total": len(hits), "hits": hits})
        return FakeResponse(404, body=b"not found", content_type="text/plain")

    def patch(self, *_args, **_kwargs):
        """Coverr's download report is a PATCH; accept it."""
        return FakeResponse(payload={"ok": True})

    def hosts_called(self):
        return {host for host, _path, _query, _headers in self.calls}
