"""Local review, approval and publishing dashboard for AI Studio."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from core.db import list_videos, update_video

IST = timezone(timedelta(hours=5, minutes=30))
st.set_page_config(page_title="AI Studio", page_icon="🎬", layout="wide")
st.title("🎬 AI Studio")
st.caption("Review generated videos, approve them, and manage YouTube publishing.")

rows = list_videos(limit=200)
total = len(rows)
uploaded = sum(bool(r.get("youtube_id")) for r in rows)
approved = sum(bool(r.get("approved")) for r in rows)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total videos", total)
m2.metric("Awaiting approval", total - approved)
m3.metric("Approved", approved)
m4.metric("Uploaded", uploaded)

review_tab, approved_tab, calendar_tab, setup_tab = st.tabs(["Video library & review", "✅ Approved videos", "Publishing calendar", "Setup"])

with review_tab:
    if not rows:
        st.info("No video jobs found yet. Generate a video with run.py, then refresh this page.")
    for row in rows:
        job_id = row["id"]
        path = Path(row.get("video_path") or "__missing_video__")
        youtube_id = row.get("youtube_id")
        with st.expander(f"#{job_id} · {row.get('title') or 'Untitled'} · {row.get('status', 'unknown').upper()}",
                         expanded=not bool(row.get("approved"))):
            preview, details = st.columns([1.3, 1])
            with preview:
                if path.is_file():
                    st.video(str(path))
                elif youtube_id:
                    st.video(f"https://www.youtube.com/watch?v={youtube_id}")
                else:
                    st.warning("Local preview unavailable. If GitHub Actions created this video, download its workflow artifact first.")
            with details:
                st.write(f"**Format:** {row.get('format') or '—'}")
                st.write(f"**Language:** {row.get('language') or '—'}")
                st.write(f"**Category:** {row.get('category') or '—'}")
                st.write(f"**Approval:** {'Approved' if row.get('approved') else 'Needs review'}")
                if youtube_id:
                    st.markdown(f"[Open YouTube video](https://youtu.be/{youtube_id})")
                    st.link_button("Open in YouTube Studio", f"https://studio.youtube.com/video/{youtube_id}/edit")
            try:
                script = json.loads(row.get("script_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                script = {}
            with st.expander("View generated script and metadata"):
                st.json(script)
            with st.expander("🖼️ Thumbnail studio", expanded=False):
                from core.config import BUILD_DIR
                thumb_dir = BUILD_DIR / f"job_{job_id}" / "thumbnails"
                options = {
                    "Bold": thumb_dir / "thumbnail_bold.jpg",
                    "Cinematic": thumb_dir / "thumbnail_cinematic.jpg",
                    "Minimal": thumb_dir / "thumbnail_minimal.jpg",
                }
                if st.button("Generate 3 AI thumbnails (no video download)", key=f"thumb_gen_{job_id}"):
                    try:
                        from core.thumbnail import generate_topic_variants
                        generated = generate_topic_variants(
                            row.get("title") or "New video", script, thumb_dir,
                            channel="AI Studio"
                        )
                        update_video(job_id, thumb_path=str(generated[0]))
                        st.success("Generated three topic-based thumbnail options.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Thumbnail generation failed: {exc}")
                available = {name: file for name, file in options.items() if file.is_file()}
                if available:
                    names = list(available)
                    saved_path = Path(row.get("thumb_path") or "")
                    default = next((name for name, file in available.items() if file == saved_path), names[0])
                    chosen = st.selectbox("Choose a design", names, index=names.index(default),
                                          key=f"thumb_choice_{job_id}")
                    chosen_path = available[chosen]
                    st.image(str(chosen_path), caption=f"{chosen} thumbnail · 1280 × 720",
                             use_container_width=True)
                    if st.button("Save selected thumbnail", key=f"thumb_save_{job_id}"):
                        update_video(job_id, thumb_path=str(chosen_path))
                        st.success("Thumbnail selection saved.")
                        st.rerun()
                    if youtube_id and st.button("Upload thumbnail to YouTube", key=f"thumb_upload_{job_id}"):
                        try:
                            from core.uploader import set_video_thumbnail
                            set_video_thumbnail(youtube_id, chosen_path)
                            update_video(job_id, thumb_path=str(chosen_path))
                            st.success("YouTube thumbnail updated.")
                        except Exception as exc:
                            st.error(f"Thumbnail upload failed: {exc}")
                elif row.get("thumb_path") and Path(row["thumb_path"]).is_file():
                    st.image(row["thumb_path"], caption="Saved thumbnail", use_container_width=True)
                else:
                    st.caption("Thumbnails are generated automatically after new videos finish rendering. You can also generate them here from the title and script.")
            a, b = st.columns(2)
            with a:
                if row.get("approved"):
                    if st.button("Revoke approval", key=f"revoke_{job_id}"):
                        update_video(job_id, approved=0, status="needs_review")
                        st.rerun()
                elif st.button("Approve video", key=f"approve_{job_id}", type="primary"):
                    update_video(job_id, approved=1, status="approved")
                    st.rerun()
            with b:
                if not youtube_id and path.is_file() and row.get("approved"):
                    if st.button("Upload as Unlisted", key=f"upload_{job_id}"):
                        try:
                            from core.uploader import upload
                            new_id = upload(path, row.get("title") or "Untitled",
                                            script.get("description", "Created with AI Studio."),
                                            script.get("tags", []), privacy_status="unlisted")
                            update_video(job_id, youtube_id=new_id, status="uploaded",
                                         privacy_status="unlisted")
                            st.success(f"Uploaded Unlisted: https://youtu.be/{new_id}")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Upload failed: {exc}")
            if youtube_id:
                st.markdown("**Schedule public release (India Standard Time)**")
                with st.form(f"schedule_{job_id}"):
                    chosen_date = st.date_input("Date", value=date.today() + timedelta(days=1), key=f"date_{job_id}")
                    chosen_time = st.time_input("Time (24-hour format)", value=time(19, 0), key=f"time_{job_id}")
                    submit = st.form_submit_button("Schedule public release")
                if submit:
                    try:
                        from core.uploader import schedule_video
                        publish_at = datetime.combine(chosen_date, chosen_time).replace(tzinfo=IST)
                        schedule_video(youtube_id, publish_at)
                        update_video(job_id, status="scheduled", scheduled_at=publish_at.isoformat(),
                                     privacy_status="private")
                        st.success(f"Scheduled for {publish_at.strftime('%d %b %Y, %H:%M')} IST.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Scheduling failed: {exc}")

with calendar_tab:
    scheduled = [r for r in rows if r.get("scheduled_at")]
    if not scheduled:
        st.info("No scheduled videos yet. Schedule a video from the review tab.")
    for row in sorted(scheduled, key=lambda item: item["scheduled_at"]):
        try:
            moment = datetime.fromisoformat(row["scheduled_at"])
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=IST)
            st.write(f"**{moment.astimezone(IST).strftime('%a, %d %b %Y · %H:%M IST')}** — {row.get('title')} (Job #{row['id']})")
        except ValueError:
            st.write(f"{row.get('scheduled_at')} — {row.get('title')}")

with setup_tab:
    st.markdown("""
    **Start the dashboard in Windows PowerShell:**

    ```powershell
    python -m pip install -r requirements.txt
    streamlit run dashboard.py
    ```

    To upload or schedule from this dashboard, configure these values in your local, uncommitted `.env` file:
    - `YT_CLIENT_ID`
    - `YT_CLIENT_SECRET`
    - `YT_REFRESH_TOKEN`

    This first version reads the local SQLite database and local video files. GitHub Actions cloud artifacts and its database are not automatically synced to your PC.
    """)
