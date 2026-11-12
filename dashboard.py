"""Local review, approval and publishing dashboard for AI Studio."""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from core.db import list_videos, update_video, create_channel_profile, list_channel_profiles, update_channel_profile, save_video
from core.config import BUILD_DIR

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

review_tab, approved_tab, calendar_tab, content_tab, analytics_tab, channels_tab, monitor_tab, setup_tab = st.tabs(["Video library & review", "✅ Approved videos", "Publishing calendar", "🧠 Content lab", "📊 YouTube analytics", "📺 Channels", "🛠️ Cost & failures", "Setup"])

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
            if row.get("quality_status"):
                st.write(f"**Video quality:** {row.get('quality_status')} · {row.get('quality_score', '—')}/100")
            with st.expander("🧪 Video quality report"):
                report_path = Path(row.get("quality_report_path") or "")
                if report_path.is_file():
                    try:
                        quality_report = json.loads(report_path.read_text(encoding="utf-8"))
                        st.metric("Quality score", f"{quality_report.get('score', '—')}/100")
                        st.write(f"**Status:** {quality_report.get('status', 'Unknown')}")
                        if quality_report.get("issues"):
                            st.error("Issues")
                            for item in quality_report["issues"]:
                                st.write(f"- {item}")
                        if quality_report.get("warnings"):
                            st.warning("Warnings")
                            for item in quality_report["warnings"]:
                                st.write(f"- {item}")
                        with st.expander("Technical measurements"):
                            st.json(quality_report.get("metrics", {}))
                    except Exception as exc:
                        st.error(f"Could not read quality report: {exc}")
                else:
                    st.info("No quality report yet. New videos are inspected automatically after rendering.")
            if row.get("format") == "longform":
                with st.expander("🎧 Documentary audio mastering review"):
                    from core.audio_quality import mark_audio_reviewed
                    audio_report_path = BUILD_DIR / f"job_{job_id}" / "audio_quality_report.json"
                    if audio_report_path.is_file():
                        try:
                            audio_report = json.loads(audio_report_path.read_text(encoding="utf-8"))
                            audio_status = audio_report.get("status", "ERROR")
                            if audio_status == "FAIL":
                                st.error("Audio QA failed. Fix the master and rebuild before approval.")
                            elif audio_status == "REVIEW":
                                st.warning("Audio QA needs a listening check. Review the full video on headphones and speakers.")
                            elif audio_status == "PASS":
                                st.success("Measured audio checks passed. A human listening review is still required.")
                            st.json(audio_report.get("metrics", {}))
                            if audio_report.get("issues"):
                                for item in audio_report["issues"]:
                                    st.error(item)
                            if audio_report.get("warnings"):
                                for item in audio_report["warnings"]:
                                    st.warning(item)
                            if audio_report.get("human_review_status") == "reviewed":
                                st.success(f"Listening review completed: {audio_report.get('reviewed_at') or 'timestamp unavailable'}")
                                if audio_report.get("reviewer_notes"):
                                    st.caption(audio_report["reviewer_notes"])
                            elif audio_status in {"PASS", "REVIEW"}:
                                audio_notes = st.text_area(
                                    "Audio listening notes (optional)",
                                    key=f"audio_notes_{job_id}",
                                    placeholder="Narration clarity, music balance, harshness, clicks, clipping, pauses…",
                                )
                                if st.button("Mark audio listening review complete", key=f"audio_review_{job_id}"):
                                    try:
                                        mark_audio_reviewed(BUILD_DIR / f"job_{job_id}", audio_notes)
                                        st.success("Audio listening review saved.")
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Could not save audio review: {exc}")
                        except Exception as exc:
                            st.error(f"Could not read audio quality report: {exc}")
                    else:
                        st.warning("Audio quality report is missing. Rebuild this documentary with Milestone 22 audio QA before approval.")
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
            if row.get("format") == "longform" and path.is_file():
                build_path = BUILD_DIR / f"job_{job_id}"
                certificate_path = build_path / "release_certificate.json"
                with st.expander("🏁 Documentary release certification"):
                    if st.button("Run complete release certification", key=f"certify_{job_id}"):
                        try:
                            from core.release_certification import certify_documentary
                            certificate = certify_documentary(build_path, path)
                            if certificate["status"] == "CERTIFIED":
                                st.success("Release certified. The video and review artifacts are hash-locked.")
                            else:
                                st.error(f"Release blocked by {len(certificate['blockers'])} issue(s).")
                                for blocker in certificate["blockers"]:
                                    st.error(blocker)
                            for warning in certificate.get("warnings", []):
                                st.warning(warning)
                        except Exception as exc:
                            st.error(f"Release certification failed: {exc}")
                    if certificate_path.is_file():
                        try:
                            certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
                            if certificate.get("status") == "CERTIFIED":
                                st.success(f"Certified at {certificate.get('created_at')}")
                                st.caption(f"Certificate ID: {certificate.get('certificate_id')}")
                                st.json(certificate.get("video_metadata", {}))
                            else:
                                st.error("Latest release certificate is BLOCKED.")
                                for blocker in certificate.get("blockers", []):
                                    st.error(blocker)
                        except Exception as exc:
                            st.error(f"Could not read release certificate: {exc}")
                    else:
                        st.info("Complete all visual, rights, audio and research reviews, then run certification here.")
            a, b = st.columns(2)
            with a:
                if row.get("approved"):
                    if st.button("Revoke approval", key=f"revoke_{job_id}"):
                        update_video(job_id, approved=0, status="needs_review")
                        st.rerun()
                elif st.button("Approve video", key=f"approve_{job_id}", type="primary"):
                    try:
                        if row.get("format") == "longform":
                            from core.release_certification import assert_release_certified
                            assert_release_certified(BUILD_DIR / f"job_{job_id}", path)
                        update_video(job_id, approved=1, status="approved")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Video approval blocked: {exc}")
            with b:
                if not youtube_id and path.is_file() and row.get("approved"):
                    if st.button("Upload as Private", key=f"upload_{job_id}"):
                        try:
                            # Shared release gate + duplicate check + channel verification;
                            # the upload is always private and unscheduled.
                            from core.publish import publish_job
                            result = publish_job(job_id, notify=False)
                            st.success(f"Uploaded as Private: {result['url']}")
                            for warning in result.get("warnings", []):
                                st.warning(f"Uploaded, but {warning}")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Upload blocked or failed: {exc}")
            if youtube_id:
                st.markdown("**Schedule public release (India Standard Time)**")
                with st.form(f"schedule_{job_id}"):
                    chosen_date = st.date_input("Date", value=date.today() + timedelta(days=1), key=f"date_{job_id}")
                    chosen_time = st.time_input("Time (24-hour format)", value=time(19, 0), key=f"time_{job_id}")
                    confirmed = st.checkbox(
                        "I have watched this video and authorise making it PUBLIC at this time.",
                        key=f"confirm_public_{job_id}")
                    submit = st.form_submit_button("Schedule public release")
                if submit and not confirmed:
                    st.error("Tick the confirmation box to authorise a public release.")
                elif submit:
                    try:
                        from core.release_gate import assert_uploadable
                        assert_uploadable(row, BUILD_DIR / f"job_{job_id}")
                        if row.get("format") == "longform":
                            from core.release_certification import assert_release_certified
                            assert_release_certified(BUILD_DIR / f"job_{job_id}", path)
                        from core.uploader import schedule_video
                        publish_at = datetime.combine(chosen_date, chosen_time).replace(tzinfo=IST)
                        # Explicit, per-video human authorisation from the form above.
                        schedule_video(youtube_id, publish_at, authorized=True)
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



with analytics_tab:
    st.subheader("📊 Performance-based content improvement")
    st.caption("Uses your authorized YouTube Analytics data. Recommendations describe correlations and test ideas; they do not claim to know why viewers behaved a certain way.")
    col_a, col_b = st.columns(2)
    with col_a:
        analytics_end = st.date_input("End date", value=date.today() - timedelta(days=2), key="perf_end")
    with col_b:
        analytics_start = st.date_input("Start date", value=date.today() - timedelta(days=31), key="perf_start")
    if analytics_start > analytics_end:
        st.error("Start date must be on or before end date.")
    elif st.button("🔄 Analyze channel performance", type="primary"):
        try:
            from core.analytics import get_channel_overview, get_daily_analytics, get_top_videos, get_video_titles
            from core.performance import rows_as_dicts, summarize_video_performance, build_content_recommendations
            with st.spinner("Reading your YouTube Analytics reports..."):
                overview = get_channel_overview()
                daily_rows, daily_headers = get_daily_analytics(analytics_start, analytics_end)
                video_rows, video_headers = get_top_videos(analytics_start, analytics_end, limit=25)
                daily = rows_as_dicts(daily_rows, daily_headers)
                videos = rows_as_dicts(video_rows, video_headers)
                video_ids = [str(x.get("video") or "") for x in videos if x.get("video")]
                titles = get_video_titles(video_ids)
                for item in videos:
                    item["title"] = titles.get(str(item.get("video") or ""), str(item.get("video") or "YouTube video"))
                summary = summarize_video_performance(videos)
                recommendations = build_content_recommendations(summary)
            st.session_state["performance_report"] = {
                "overview": overview, "daily": daily, "summary": summary,
                "recommendations": recommendations,
                "start": analytics_start.isoformat(), "end": analytics_end.isoformat()
            }
        except Exception as exc:
            st.error(f"Analytics request failed: {exc}")
            st.info("Check that your OAuth refresh token includes youtube.readonly and yt-analytics.readonly, and that YouTube Analytics API is enabled.")
    report = st.session_state.get("performance_report")
    if report:
        st.caption(f"Reporting period: {report['start']} to {report['end']}")
        overview = report["overview"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Channel views", f"{overview['views']:,}")
        k2.metric("Subscribers", f"{overview['subscribers']:,}")
        k3.metric("Channel videos", f"{overview['videos']:,}")
        k4.metric("Top videos analyzed", len(report["summary"]["videos"]))
        daily = report["daily"]
        if daily:
            st.markdown("#### Daily views")
            try:
                import pandas as pd
                chart_data = pd.DataFrame([{"Date": x.get("day"), "Views": int(x.get("views", 0) or 0)} for x in daily])
                st.line_chart(chart_data.set_index("Date"))
            except Exception:
                st.dataframe(daily, use_container_width=True)
        summary = report["summary"]
        st.markdown("#### What the data says")
        for insight in summary["insights"]:
            st.write("• " + insight)
        if summary["videos"]:
            st.markdown("#### Top video performance")
            table = [{
                "Video": x.get("title") or x.get("video"),
                "Views": x["views"],
                "Avg view duration (sec)": round(x["average_view_duration_seconds"], 1),
                "Likes": x["likes"], "Comments": x["comments"], "Shares": x["shares"],
                "Engagement / view (%)": round(x["engagement_per_view_pct"], 2)
            } for x in summary["videos"]]
            st.dataframe(table, use_container_width=True, hide_index=True)
        st.markdown("#### Recommended experiments for upcoming videos")
        for recommendation in report["recommendations"]:
            st.write("• " + recommendation)
        st.caption("Metrics are limited to the selected date range and returned top 25 videos. Average view duration is not audience-retention percentage. Compare similar formats and durations; do not infer causation from a single report.")
    else:
        st.info("Choose a date range and click Analyze channel performance. No analytics are fetched until you request them.")

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


with monitor_tab:
    st.subheader("🛠️ Cost & Failure Monitor")
    st.caption("Local telemetry from instrumented app operations. Events are recorded without prompts, API keys or tokens.")
    from core.monitoring import summarize_events, resource_snapshot, configured_cost_estimate, read_events
    report = summarize_events()
    snap = resource_snapshot()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Recorded events", report["total_events"])
    c2.metric("Successful operations", report["successes"])
    c3.metric("Failures", report["failures"])
    c4.metric("Warnings", report["warnings"])
    left, right = st.columns(2)
    with left:
        st.markdown("#### Resource usage")
        st.metric("Output folder", f"{snap['output_bytes'] / (1024**3):.2f} GB")
        st.metric("Build folder", f"{snap['build_bytes'] / (1024**3):.2f} GB")
        st.metric("Free disk space", f"{snap['disk_free_bytes'] / (1024**3):.2f} GB")
    with right:
        st.markdown("#### Cost visibility")
        cost = configured_cost_estimate()
        st.info(cost["reason"])
        if cost["configured_unit_rates"]:
            st.write("Configured unit rates:", cost["configured_unit_rates"])
        else:
            st.caption("No unit rates configured. Monetary cost is not estimated.")
    st.markdown("#### Failures by component")
    if report["failure_components"]:
        st.bar_chart(report["failure_components"])
    else:
        st.success("No recorded failures in the available local event log.")
    st.markdown("#### Recent failures")
    if report["recent_failures"]:
        st.dataframe([{
            "Time": e.get("timestamp"), "Component": e.get("component"),
            "Action": e.get("action"), "Error type": e.get("error_type"),
            "Message": e.get("error")
        } for e in report["recent_failures"]], use_container_width=True, hide_index=True)
    else:
        st.caption("No failures recorded yet.")
    with st.expander("Recent operation events"):
        st.dataframe([{
            "Time": e.get("timestamp"), "Component": e.get("component"),
            "Action": e.get("action"), "Status": e.get("status"),
            "Duration (s)": e.get("duration_seconds")
        } for e in report["events"][-100:][::-1]], use_container_width=True, hide_index=True)
    st.caption("The event log is local to the machine/container running this dashboard. GitHub Actions runner logs and local dashboard logs are separate; they are not automatically synchronized.")
    

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
