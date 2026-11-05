"""Content calendar and approval-gated YouTube scheduler."""
import streamlit as st
from datetime import date, datetime, time, timedelta, timezone

from core.db import list_videos, update_video
from core.scheduler import apply_planned_schedules, IST

st.set_page_config(page_title="Content Calendar", page_icon="🗓️", layout="wide")
st.title("🗓️ Content Calendar & Auto Scheduler")
st.caption("Plan publishing slots in India Standard Time, then apply them to approved videos already uploaded to YouTube.")

rows = list_videos(limit=300)
if not rows:
    st.info("No video jobs yet. Create scripts or videos first.")
    st.stop()

st.subheader("Plan a publishing slot")
with st.form("plan_publish_slot"):
    candidates = [r for r in rows if not r.get("scheduled_at")]
    if not candidates:
        st.info("All current jobs are already scheduled.")
        selected_id = None
    else:
        labels = [f"#{r['id']} · {r.get('title') or 'Untitled'} · {'Approved' if r.get('approved') else 'Needs approval'} · {'Uploaded' if r.get('youtube_id') else 'Not uploaded'}" for r in candidates]
        selected_label = st.selectbox("Video job", labels)
        selected_id = candidates[labels.index(selected_label)]["id"]
    col1, col2 = st.columns(2)
    with col1:
        plan_date = st.date_input("Publish date", value=date.today() + timedelta(days=1), min_value=date.today())
    with col2:
        plan_time = st.time_input("Publish time (IST)", value=time(19, 0), step=900)
    save_plan = st.form_submit_button("Save publishing plan", type="primary", disabled=selected_id is None)

if save_plan and selected_id is not None:
    moment = datetime.combine(plan_date, plan_time).replace(tzinfo=IST)
    if moment <= datetime.now(IST) + timedelta(minutes=15):
        st.error("Choose a time at least 15 minutes in the future.")
    else:
        update_video(selected_id, planned_publish_at=moment.isoformat())
        st.success("Publishing slot saved. It will not publish until you apply the schedule below.")
        st.rerun()

st.divider()
st.subheader("Weekly publishing plan")
st.caption("Assign a sequence of approved or draft video jobs to consecutive selected weekdays. This only reserves calendar slots; it does not upload or publish anything.")
with st.form("weekly_plan"):
    available = [r for r in rows if not r.get("scheduled_at") and not r.get("planned_publish_at")]
    available_labels = [f"#{r['id']} · {r.get('title') or 'Untitled'}" for r in available]
    chosen_labels = st.multiselect("Videos to place in the calendar", available_labels)
    weekdays = st.multiselect("Publishing days", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], default=["Monday", "Wednesday", "Friday"])
    first_day = st.date_input("Start date", value=date.today() + timedelta(days=1), key="weekly_start")
    slot_time = st.time_input("Usual publishing time (IST)", value=time(19, 0), key="weekly_time")
    create_week = st.form_submit_button("Create weekly plan")
if create_week:
    if not chosen_labels or not weekdays:
        st.error("Choose at least one video and one publishing day.")
    else:
        day_numbers = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
        allowed_days = {day_numbers[x] for x in weekdays}
        chosen_rows = [available[available_labels.index(x)] for x in chosen_labels]
        cursor = first_day
        created = 0
        for item in chosen_rows:
            while cursor.weekday() not in allowed_days:
                cursor += timedelta(days=1)
            moment = datetime.combine(cursor, slot_time).replace(tzinfo=IST)
            if moment <= datetime.now(IST) + timedelta(minutes=15):
                cursor += timedelta(days=1)
                continue
            update_video(item["id"], planned_publish_at=moment.isoformat())
            created += 1
            cursor += timedelta(days=1)
        st.success(f"Added {created} video(s) to the calendar.")
        st.rerun()

st.divider()
st.subheader("Calendar queue")
rows = list_videos(limit=300)
planned = [r for r in rows if r.get("planned_publish_at") or r.get("scheduled_at")]
planned.sort(key=lambda r: r.get("scheduled_at") or r.get("planned_publish_at") or "")
if not planned:
    st.info("Your calendar is empty. Add a publishing slot above.")
for item in planned:
    stamp = item.get("scheduled_at") or item.get("planned_publish_at")
    try:
        moment = datetime.fromisoformat(stamp)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=IST)
        display_time = moment.astimezone(IST).strftime("%a, %d %b %Y · %H:%M IST")
    except ValueError:
        display_time = stamp
    state = "Published/scheduled on YouTube" if item.get("scheduled_at") else ("Ready to schedule" if item.get("approved") and item.get("youtube_id") else "Planned — awaiting approval/upload")
    with st.container(border=True):
        left, right = st.columns([3, 1])
        left.markdown(f"**{display_time}**  \\n{item.get('title') or 'Untitled'} · Job #{item['id']}  \\n{state}")
        if not item.get("scheduled_at") and right.button("Remove slot", key=f"remove_{item['id']}"):
            update_video(item["id"], planned_publish_at=None)
            st.rerun()

st.divider()
st.subheader("Apply saved schedules to YouTube")
st.warning("This action changes YouTube visibility scheduling. Only approved, already-uploaded videos with a saved future slot are eligible. Drafts and unuploaded jobs remain in the calendar.")
if st.button("🚀 Schedule all eligible videos on YouTube", type="primary"):
    try:
        from core.uploader import schedule_video
        results = apply_planned_schedules(
            rows, schedule_video, update_video,
            now=datetime.now(timezone.utc))
        if not results:
            st.info("No approved, uploaded videos are ready to schedule.")
        for result in results:
            if result["status"] == "scheduled":
                st.success(f"Job #{result['id']} scheduled for {result['scheduled_at']}.")
            elif result["status"] == "too_soon":
                st.warning(f"Job #{result['id']}: selected time is less than 15 minutes away.")
            elif result["status"] == "already_scheduled":
                st.info(f"Job #{result['id']} is already scheduled.")
            else:
                st.error(f"Job #{result['id']} failed: {result.get('error')}")
    except Exception as exc:
        st.error(f"Could not access YouTube scheduling: {exc}")

st.caption("Scheduling uses your connected YouTube OAuth account and YouTube's native scheduled-publishing feature. This page does not run unattended in the background; you must open it and apply the saved schedule. The local database is separate from GitHub Actions' database unless you sync it.")
