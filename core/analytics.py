"""Read-only YouTube channel analytics for the AI Studio dashboard."""
from datetime import date, timedelta

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from core.config import YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


def _services():
    missing = [name for name, value in (
        ("YT_CLIENT_ID", YT_CLIENT_ID),
        ("YT_CLIENT_SECRET", YT_CLIENT_SECRET),
        ("YT_REFRESH_TOKEN", YT_REFRESH_TOKEN),
    ) if not value or not value.strip()]
    if missing:
        raise RuntimeError("Missing YouTube credentials: " + ", ".join(missing))
    creds = Credentials(
        None,
        refresh_token=YT_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        scopes=[ANALYTICS_SCOPE, YOUTUBE_READ_SCOPE],
    )
    creds.refresh(Request())
    return (
        build("youtube", "v3", credentials=creds, cache_discovery=False),
        build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False),
    )


def get_channel_overview():
    youtube, _ = _services()
    response = youtube.channels().list(part="snippet,statistics", mine=True).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("No YouTube channel was found for the authorized Google account.")
    item = items[0]
    stats = item.get("statistics", {})
    return {
        "channel_title": item.get("snippet", {}).get("title", "YouTube channel"),
        "channel_id": item.get("id", ""),
        "views": int(stats.get("viewCount", 0)),
        "subscribers": int(stats.get("subscriberCount", 0)),
        "videos": int(stats.get("videoCount", 0)),
    }


def get_daily_analytics(start_date: date, end_date: date):
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    _, analytics = _services()
    result = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,likes,comments",
        dimensions="day",
        sort="day",
    ).execute()
    return result.get("rows", []), result.get("columnHeaders", [])


def get_top_videos(start_date: date, end_date: date, limit: int = 10):
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    _, analytics = _services()
    result = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration,likes,comments,shares",
        dimensions="video",
        sort="-views",
        maxResults=max(1, min(int(limit), 25)),
    ).execute()
    return result.get("rows", []), result.get("columnHeaders", [])
