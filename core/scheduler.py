"""Apply saved publishing plans to approved, already-uploaded YouTube videos."""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def apply_planned_schedules(rows, schedule_func, update_func, now=None):
    """Schedule only approved uploaded videos with a valid future planned time.

    Returns a per-video result list. Drafts and videos without YouTube IDs are
    left planned and untouched until they are uploaded.
    """
    now = now or datetime.now(timezone.utc)
    results = []
    for row in rows:
        planned = row.get("planned_publish_at")
        if not planned or not row.get("approved") or not row.get("youtube_id"):
            continue
        if row.get("scheduled_at"):
            results.append({"id": row["id"], "status": "already_scheduled"})
            continue
        try:
            moment = datetime.fromisoformat(planned)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=IST)
            if moment.astimezone(timezone.utc) <= now.astimezone(timezone.utc) + timedelta(minutes=15):
                results.append({"id": row["id"], "status": "too_soon"})
                continue
            schedule_func(row["youtube_id"], moment)
            update_func(row["id"], status="scheduled",
                        scheduled_at=moment.isoformat(), privacy_status="private")
            results.append({"id": row["id"], "status": "scheduled",
                            "scheduled_at": moment.isoformat()})
        except Exception as exc:
            results.append({"id": row["id"], "status": "error", "error": str(exc)})
    return results
