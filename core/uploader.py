"""YouTube uploader with scheduled publishing and playlist support."""
import datetime, time
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from core.config import (YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, PRIVACY, PUBLISH_AT_IST)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

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
           category_id: str = "27", contains_synthetic: bool = True,\n           privacy_status: str | None = None) -> str:
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    status = {"selfDeclaredMadeForKids": False, "containsSyntheticMedia": contains_synthetic}
    pt = _publish_time()
    if pt:
        status.update({"privacyStatus": "private", "publishAt": pt})
    else:
        status["privacyStatus"] = privacy_status or PRIVACY or "unlisted"
    body = {"snippet": {"title": title[:100], "description": description,
                        "tags": tags[:30], "categoryId": category_id},
            "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                              media_body=MediaFileUpload(str(video_path),
                                                         mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk(num_retries=5)
    return resp["id"]

def schedule_video(vid: str, publish_at: datetime.datetime):
    """Schedule an already-uploaded video; input must be timezone-aware."""
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

def set_video_privacy(vid: str, privacy: str):
    if privacy not in {"private", "unlisted", "public"}:
        raise ValueError("Privacy must be private, unlisted, or public.")
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
                  "status": {"privacyStatus": "public"}}).execute()["id"]
    yt.playlistItems().insert(part="snippet",
        body={"snippet": {"playlistId": pid,
                          "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()

def post_comment(vid: str, text: str):
    yt = build("youtube", "v3", credentials=_creds(), cache_discovery=False)
    yt.commentThreads().insert(part="snippet",
        body={"snippet": {"videoId": vid,
                          "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()

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
