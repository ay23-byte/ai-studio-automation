"""YouTube uploader: private by default, channel-verified and idempotent.

Safety model (see docs/ARCHITECTURE.md, "Publishing safety"):

* Every upload is PRIVATE and unscheduled unless the operator explicitly opts in
  with ``ALLOW_PUBLIC_PUBLISH=1`` (environment) or ``authorized=True`` (a human
  pressing a clearly labelled dashboard button).
* Before the first write the authenticated channel is compared with
  ``YT_CHANNEL_ID`` / ``YT_CHANNEL_HANDLE``; a refresh token for a different
  channel can no longer upload to the wrong place.
* An idempotency marker in the description lets a retried job find its earlier
  upload instead of creating a duplicate.
"""
import datetime, os, time
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from core import config as _cfg
from core.config import (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, PRIVACY, PUBLISH_AT_IST)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
_VERIFIED_CHANNEL = {}


class ChannelMismatchError(RuntimeError):
    """The OAuth credentials belong to a channel other than the configured one."""


class PublicPublishNotAuthorized(RuntimeError):
    """A non-private visibility change was requested without explicit authorization."""


def public_publish_allowed() -> bool:
    return os.getenv("ALLOW_PUBLIC_PUBLISH", "").strip().lower() in {"1", "true", "yes"}


def resolve_privacy(requested=None, authorized=False) -> str:
    """Visibility actually sent to YouTube. Anything but private needs opt-in."""
    value = str(requested or PRIVACY or "private").strip().lower()
    if value not in {"private", "unlisted", "public"}:
        value = "private"
    if value != "private" and not (authorized or public_publish_allowed()):
        print(f"Upload visibility '{value}' was requested but public publishing is not "
              "authorized (ALLOW_PUBLIC_PUBLISH is not set); uploading as PRIVATE.")
        return "private"
    return value


def _normalise_handle(value) -> str:
    return str(value or "").strip().lower().lstrip("@").rstrip("/").split("/")[-1].lstrip("@")


def verify_channel(yt=None, force=False) -> dict:
    """Confirm the credentials belong to the configured channel; returns its identity.

    Set ``YT_CHANNEL_ID`` (most reliable) and/or ``YT_CHANNEL_HANDLE``. Set
    ``YT_CHANNEL_VERIFY=0`` only for deliberate uploads to another channel.
    """
    if os.getenv("YT_CHANNEL_VERIFY", "1").strip().lower() in {"0", "false", "no"}:
        return {"verified": False, "skipped": True}
    expected_id = str(getattr(_cfg, "YT_CHANNEL_ID", "") or "").strip()
    expected_handle = _normalise_handle(getattr(_cfg, "YT_CHANNEL_HANDLE", ""))
    if not expected_id and not expected_handle:
        return {"verified": False, "skipped": True}
    if _VERIFIED_CHANNEL and not force:
        return dict(_VERIFIED_CHANNEL)
    yt = yt or build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    response = yt.channels().list(part="id,snippet", mine=True).execute(num_retries=3)
    items = response.get("items") or []
    if not items:
        raise ChannelMismatchError(
            "The YouTube credentials are not attached to any channel; refusing to upload.")
    channel = items[0]
    snippet = channel.get("snippet") or {}
    actual_id = str(channel.get("id") or "")
    actual_handle = _normalise_handle(snippet.get("customUrl"))
    if expected_id and actual_id != expected_id:
        raise ChannelMismatchError(
            f"Authenticated channel {actual_id} ({snippet.get('title')}) is not the configured "
            f"YT_CHANNEL_ID {expected_id}; refusing to upload to the wrong channel.")
    if not expected_id and expected_handle and actual_handle != expected_handle:
        raise ChannelMismatchError(
            f"Authenticated channel handle @{actual_handle or 'unknown'} ({snippet.get('title')}) "
            f"is not the configured YT_CHANNEL_HANDLE @{expected_handle}; refusing to upload. "
            "Re-create YT_REFRESH_TOKEN while signed in to the correct channel, or set YT_CHANNEL_ID.")
    _VERIFIED_CHANNEL.update({"verified": True, "id": actual_id, "title": snippet.get("title"),
                              "handle": "@" + actual_handle if actual_handle else ""})
    print(f"YouTube channel verified: {snippet.get('title')} ({_VERIFIED_CHANNEL['handle'] or actual_id}).")
    return dict(_VERIFIED_CHANNEL)


def _creds():
    missing = [
        name for name, value in (
            ("YT_CLIENT_ID", YT_CLIENT_ID),
            ("YT_CLIENT_SECRET", YT_CLIENT_SECRET),
            ("YT_REFRESH_TOKEN", YT_REFRESH_TOKEN),
        ) if not value or not value.strip()
    ]
    if missing:
        raise RuntimeError(
            "YouTube upload is not configured. Missing credentials: "
            + ", ".join(missing)
            + ". Add them under GitHub Settings > Secrets and variables > Actions "
              "or to your local .env file. Never commit credentials."
        )
    c = Credentials(None, refresh_token=YT_REFRESH_TOKEN,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=YT_CLIENT_ID, client_secret=YT_CLIENT_SECRET)
    c.refresh(Request())
    return c

def _publish_time():
    if not PUBLISH_AT_IST:
        return None
    try:
        hh, mm = (int(x) for x in PUBLISH_AT_IST.split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        raise ValueError("PUBLISH_AT_IST must use 24-hour HH:MM format, e.g. 19:00")
    now = datetime.datetime.now(IST)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now + datetime.timedelta(minutes=15):
        target += datetime.timedelta(days=1)
    return target.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def upload(video_path: Path, title: str, description: str, tags: list,
           category_id: str = "27", contains_synthetic: bool = True,
           privacy_status: str | None = None,
           disable_scheduling: bool = False, *,
           marker: str | None = None, authorized: bool = False) -> str:
    """Upload a video as PRIVATE unless public publishing is explicitly authorized.

    ``disable_scheduling`` guarantees a private upload with no ``publishAt``.
    ``marker`` is an idempotency token appended to the description: when a
    private upload carrying it already exists, that video id is returned and
    nothing is uploaded again.
    """
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    verify_channel(yt)
    description = str(description or "")
    if marker:
        existing = find_private_video_by_marker(marker)
        if existing:
            print(f"An upload carrying marker {marker} already exists ({existing}); not uploading again.")
            return existing
        if marker not in description:
            description = (description.rstrip() + "\n\n" + marker).strip()
    status = {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": contains_synthetic}
    may_publish = not disable_scheduling and (authorized or public_publish_allowed())
    pt = _publish_time() if may_publish else None
    if not may_publish and not disable_scheduling and PUBLISH_AT_IST:
        print("PUBLISH_AT_IST is set but public publishing is not authorized "
              "(ALLOW_PUBLIC_PUBLISH is not set); uploading PRIVATE with no publish time.")
    if pt:
        status.update({"privacyStatus": "private", "publishAt": pt})
    else:
        status["privacyStatus"] = "private" if disable_scheduling else resolve_privacy(privacy_status, authorized)
    body = {"snippet": {"title": title[:100], "description": description[:5000],
                        "tags": list(tags or [])[:30], "categoryId": category_id},
            "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(str(video_path),
                                                         mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    return resp["id"]


def get_video_status(vid: str) -> dict:
    """Read back the privacy/processing state YouTube actually stored."""
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    items = yt.videos().list(part="status,snippet", id=vid).execute(num_retries=3).get("items") or []
    if not items:
        return {"found": False}
    status = items[0].get("status") or {}
    return {"found": True, "privacy_status": status.get("privacyStatus"),
            "publish_at": status.get("publishAt"), "upload_status": status.get("uploadStatus"),
            "channel_id": (items[0].get("snippet") or {}).get("channelId"),
            "title": (items[0].get("snippet") or {}).get("title")}


def schedule_video(vid: str, publish_at: datetime.datetime, *, authorized: bool = False):
    """Schedule an already-uploaded video for PUBLIC release; input must be timezone-aware.

    This makes a video public at ``publish_at``, so it requires explicit
    authorization: ``authorized=True`` from a human-triggered action, or
    ``ALLOW_PUBLIC_PUBLISH=1``.
    """
    if not (authorized or public_publish_allowed()):
        raise PublicPublishNotAuthorized(
            "Scheduling a public release requires explicit authorization "
            "(a confirmed dashboard action or ALLOW_PUBLIC_PUBLISH=1).")
    if publish_at.tzinfo is None:
        raise ValueError("Publishing date/time must include a timezone.")
    publish_utc = publish_at.astimezone(datetime.timezone.utc)
    if publish_utc <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15):
        raise ValueError("Choose a publishing time at least 15 minutes in the future.")
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    yt.videos().update(
        part="status",
        body={"id": vid, "status": {
            "privacyStatus": "private",
            "publishAt": publish_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        }}
    ).execute()

def set_video_privacy(vid: str, privacy: str, *, authorized: bool = False):
    if privacy not in {"private", "unlisted", "public"}:
        raise ValueError("Privacy must be private, unlisted, or public.")
    if privacy != "private" and not (authorized or public_publish_allowed()):
        raise PublicPublishNotAuthorized(
            f"Changing visibility to {privacy} requires explicit authorization "
            "(a confirmed dashboard action or ALLOW_PUBLIC_PUBLISH=1).")
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    yt.videos().update(
        part="status",
        body={"id": vid, "status": {"privacyStatus": privacy}}
    ).execute()

def add_to_playlist(vid: str, playlist_title: str):
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    pid = None
    r = yt.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    for it in r.get("items", []):
        if it["snippet"]["title"] == playlist_title:
            pid = it["id"]
            break
    if not pid:
        pid = yt.playlists().insert(part="snippet,status",
            body={"snippet": {"title": playlist_title},
                  # New playlists start private; a private video in a public
                  # playlist would still advertise its title.
                  "status": {"privacyStatus": resolve_privacy(os.getenv("PLAYLIST_PRIVACY", "private"))}}
        ).execute()["id"]
    yt.playlistItems().insert(part="snippet",
        body={"snippet": {"playlistId": pid,
                          "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()

def post_comment(vid: str, text: str):
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    yt.commentThreads().insert(part="snippet",
        body={"snippet": {"videoId": vid,
                          "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()


def _marker_match(yt, video_ids, marker: str) -> str | None:
    """Return the id of the video whose description carries ``marker``.

    Raises when such a video exists but is not private: that needs a human, not
    a second upload.
    """
    ids = [str(item) for item in video_ids if item]
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        details = yt.videos().list(part="snippet,status", id=",".join(batch)).execute(num_retries=5)
        for video in details.get("items", []):
            description = str((video.get("snippet") or {}).get("description") or "")
            if marker not in description:
                continue
            video_id = str(video.get("id") or (batch[0] if len(batch) == 1 else ""))
            if not video_id:
                raise RuntimeError("YouTube returned a matching video without an id; refusing to upload again.")
            privacy = str((video.get("status") or {}).get("privacyStatus") or "")
            if privacy != "private":
                raise RuntimeError(
                    f"Existing video {video_id} has the source-run marker but is not private; "
                    "refusing another upload. Review its privacy manually.")
            return video_id
    return None


def _recent_upload_ids(yt, limit: int = 100) -> list[str]:
    """Ids of the channel's most recent uploads, read from its uploads playlist.

    Unlike ``search().list`` this has no indexing delay, so an upload made a
    minute ago is found.  One quota unit per page instead of 100.
    """
    channels = yt.channels().list(part="contentDetails", mine=True).execute(num_retries=5)
    items = channels.get("items") or []
    if not isinstance(items, list) or not items:
        return []
    playlist = (((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
    if not isinstance(playlist, str) or not playlist:
        return []
    found, token = [], None
    while len(found) < limit:
        page = yt.playlistItems().list(part="contentDetails", playlistId=playlist, maxResults=50,
                                       pageToken=token).execute(num_retries=5)
        for item in page.get("items") or []:
            video_id = (item.get("contentDetails") or {}).get("videoId")
            if video_id:
                found.append(str(video_id))
        token = page.get("nextPageToken")
        if not isinstance(token, str) or not token:
            break
    return found[:limit]


def find_private_video_by_marker(marker: str) -> str | None:
    """Find an existing private upload carrying this immutable idempotency marker.

    The channel's recent uploads are checked first (no indexing delay), then
    YouTube search.  Any API failure is raised to the caller: uncertainty must
    never trigger a duplicate upload.
    """
    marker = str(marker or "").strip()
    if not marker:
        raise ValueError("An upload idempotency marker is required.")
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    recent = _marker_match(yt, _recent_upload_ids(yt), marker)
    if recent:
        return recent
    response = yt.search().list(
        part="id", forMine=True, type="video", q=marker, maxResults=50
    ).execute(num_retries=5)
    return _marker_match(yt, [(item.get("id") or {}).get("videoId") for item in response.get("items", [])],
                         marker)


def wait_processed(vid: str, max_wait: int = 600) -> str:
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    start = time.time()
    while time.time() - start < max_wait:
        r = yt.videos().list(part="status", id=vid).execute()
        items = r.get("items", [])
        if not items:
            return "not_found"
        us = items[0].get("status", {}).get("uploadStatus", "")
        if us == "processed":
            return "ok"
        if us in ("failed", "rejected", "deleted"):
            return us
        time.sleep(20)
    return "timeout"

def set_video_thumbnail(vid: str, image_path: Path):
    """Set a custom thumbnail on an existing YouTube video."""
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Thumbnail file not found: {image_path}")
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    yt.thumbnails().set(
        videoId=vid,
        media_body=MediaFileUpload(str(image_path), mimetype="image/jpeg")
    ).execute()
