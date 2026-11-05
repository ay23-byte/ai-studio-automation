"""Local review, approval and publishing dashboard for AI Studio."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from core.db import list_videos, update_video, create_channel_profile, list_channel_profiles, update_channel_profile, save_video

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

review_tab, approved_tab, calendar_tab, content_tab, analytics_tab, channels_tab, setup_tab = st.tabs(["Video library & review", "✅ Approved videos", "Publishing calendar", "🧠 Content lab", "📊 YouTube analytics", "📺 Channels", "Setup"])

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
                profiles_for_job = list_channel_profiles(active_only=True)
                if profiles_for_job:
                    choices = ["Unassigned"] + [f"{p['name']} (#{p['id']})" for p in profiles_for_job]
                    current_id = row.get("channel_profile_id")
                    selected_index = next((i + 1 for i, p in enumerate(profiles_for_job) if p["id"] == current_id), 0)
                    chosen_profile = st.selectbox("Channel workspace", choices, index=selected_index, key=f"channel_assign_{job_id}")
                    if st.button("Save channel assignment", key=f"channel_save_{job_id}"):
                        chosen_id = None if chosen_profile == "Unassigned" else profiles_for_job[choices.index(chosen_profile) - 1]["id"]
                        update_video(job_id, channel_profile_id=chosen_id)
                        st.success("Channel assignment saved.")
                        st.rerun()
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


with channels_tab:
    st.subheader("📺 Channel workspaces")
    st.caption("Organize content by channel brand, niche, language and editorial voice.")

    st.markdown("#### ✍️ Generate a channel-branded script")
    active_profiles = list_channel_profiles(active_only=True)
    if active_profiles:
        with st.form("channel_script_generator"):
            profile_labels = [f"{p['name']} (#{p['id']})" for p in active_profiles]
            selected_label = st.selectbox("Channel", profile_labels)
            selected_profile = active_profiles[profile_labels.index(selected_label)]
            topic = st.text_input("Video topic", placeholder="e.g. How black holes bend space-time")
            script_format = st.selectbox("Video format", ["shorts", "longform"])
            audience = st.text_input("Target audience", value=selected_profile["niche"] or "Beginners")
            duration = st.slider("Long-form duration (minutes)", 3, 20, 7) if script_format == "longform" else 0
            generate_script = st.form_submit_button("Generate channel-branded script", type="primary")
        if generate_script:
            if not topic.strip():
                st.error("Enter a topic first.")
            else:
                try:
                    from core.script_engine import generate_shorts_script, generate_longform_script
                    with st.spinner(f"Writing for {selected_profile['name']}..."):
                        if script_format == "shorts":
                            generated = generate_shorts_script(
                                topic, selected_profile["category"],
                                channel_name=selected_profile["name"],
                                audience=audience,
                                brand_voice=selected_profile["brand_voice"])
                        else:
                            lang = selected_profile["language"].title()
                            generated = generate_longform_script(
                                topic, selected_profile["category"],
                                max(2, duration - 1), duration, lang,
                                channel_name=selected_profile["name"],
                                audience=audience,
                                brand_voice=selected_profile["brand_voice"])
                    generated["channel_name"] = selected_profile["name"]
                    generated["channel_profile_id"] = selected_profile["id"]
                    job_id = save_video(
                        generated.get("title", topic), script_format,
                        selected_profile["language"], selected_profile["category"],
                        generated, channel_profile_id=selected_profile["id"])
                    st.success(f"Script saved as draft job #{job_id}. Review it in the Video library tab before rendering or publishing.")
                    st.json(generated)
                except Exception as exc:
                    st.error(f"Script generation failed: {exc}")
    else:
        st.info("Create and activate a channel workspace before generating a branded script.")

    with st.expander("➕ Add a channel workspace", expanded=not bool(list_channel_profiles())):
        with st.form("create_channel_profile"):
            new_name = st.text_input("Channel name", placeholder="e.g. Physics Sparks")
            new_handle = st.text_input("YouTube handle (optional)", placeholder="@yourchannel")
            new_niche = st.text_input("Niche", placeholder="Physics education")
            new_language = st.selectbox("Default language", ["hindi", "hinglish", "english"])
            new_category = st.text_input("Default category", value="education")
            new_voice = st.text_area("Brand voice / editorial rules", placeholder="Audience, tone, style and words to avoid.")
            create = st.form_submit_button("Create channel workspace", type="primary")
        if create:
            try:
                create_channel_profile(new_name, new_handle, new_niche, new_language, new_category, new_voice)
                st.success("Channel workspace created.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    profiles = list_channel_profiles()
    if profiles:
        for profile in profiles:
            with st.expander(f"{profile['name']} - {profile['niche'] or 'No niche set'}"):
                st.write(f"Handle: {profile['handle'] or 'Not set'}")
                st.write(f"Language: {profile['language']} | Category: {profile['category']}")
                st.write(f"Brand voice: {profile['brand_voice'] or 'Not defined'}")
                with st.form(f"edit_channel_{profile['id']}"):
                    edit_name = st.text_input("Name", value=profile["name"])
                    edit_handle = st.text_input("Handle", value=profile["handle"])
                    edit_niche = st.text_input("Niche", value=profile["niche"])
                    edit_language = st.selectbox("Language", ["hindi", "hinglish", "english"], index=["hindi", "hinglish", "english"].index(profile["language"]))
                    edit_category = st.text_input("Category", value=profile["category"])
                    edit_voice = st.text_area("Brand voice", value=profile["brand_voice"])
                    edit_active = st.checkbox("Active", value=bool(profile["active"]))
                    save_profile = st.form_submit_button("Save workspace")
                if save_profile:
                    try:
                        update_channel_profile(profile["id"], name=edit_name, handle=edit_handle, niche=edit_niche, language=edit_language, category=edit_category, brand_voice=edit_voice, active=int(edit_active))
                        st.success("Channel workspace updated.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    else:
        st.info("Create your first channel workspace to organize video jobs.")
    st.warning("These profiles organize content only. Uploading and analytics still use the single Google account configured by YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN. Separate YouTube OAuth accounts are not connected yet.")

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
