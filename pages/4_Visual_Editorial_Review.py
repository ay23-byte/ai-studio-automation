"""Streamlit page for shot-by-shot documentary editorial approval."""
import json
from pathlib import Path

import streamlit as st

from core.config import BUILD_DIR
from core.editorial_review import (
    ReviewStoreError, audit_visual_reviews, load_reviews, review_store_path, save_review,
)

st.title("🎞️ Visual editorial review")
st.caption("Review every documentary shot before publication. Approval is not proof of factual accuracy, authenticity, rights, or lip-sync.")

reports = sorted(BUILD_DIR.rglob("visual_review_report.json"), key=lambda p: p.stat().st_mtime, reverse=True)
if not reports:
    st.info("No visual review manifests found yet. Generate a long-form documentary first.")
    st.stop()

report_labels = [f"{path.parent.name} · {path}" for path in reports]
selected_label = st.selectbox("Documentary build", report_labels)
report_path = reports[report_labels.index(selected_label)]
try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    shots = report.get("shots") or []
    if not isinstance(shots, list):
        raise ValueError("The report's shots field must be a list.")
    store = load_reviews(report_path, shots)
except (OSError, json.JSONDecodeError, ValueError, ReviewStoreError) as exc:
    st.error(f"Cannot load this review manifest safely: {exc}")
    st.stop()

gate = audit_visual_reviews(report, store)
release_issues = list(gate["issues"])
try:
    from core.audio_quality import assert_audio_quality_ready
    assert_audio_quality_ready(report_path.parent)
except Exception as exc:
    release_issues.append({"shot_id": "audio", "message": str(exc)})
try:
    from core.research_verification import assert_research_review_ready
    assert_research_review_ready(report_path.parent)
except Exception as exc:
    release_issues.append({"shot_id": "research", "message": str(exc)})
a, b, c, d = st.columns(4)
a.metric("Shots", gate["shot_count"])
b.metric("Approved", gate["approved_count"])
c.metric("Pending", gate["pending_count"])
d.metric("Needs replacement", gate["replacement_count"])
if not release_issues:
    st.success("Combined long-form review gate passed: editorial, source, rights, audio listening and claim verification are complete.")
else:
    st.error(f"Publication blocked: {len(release_issues)} review issue(s) remain across visual, rights, audio or research checks.")

records = store["shots"]
chapters = sorted({records.get(str(s.get("shot_id")), {}).get("chapter_id", "unassigned") for s in shots})
chapter_filter = st.selectbox("Filter chapter", ["All"] + chapters)
status_filter = st.selectbox("Filter status", ["All", "pending", "approved", "rejected", "needs_replacement"])
visible = []
for index, shot in enumerate(shots):
    shot_id = str(shot.get("shot_id") or f"shot_{index + 1:04d}")
    record = records.get(shot_id, {})
    if chapter_filter != "All" and record.get("chapter_id", "unassigned") != chapter_filter:
        continue
    if status_filter != "All" and record.get("status", "pending") != status_filter:
        continue
    visible.append((shot_id, shot, record))

st.write(f"Showing {len(visible)} of {len(shots)} shots.")
for shot_id, shot, record in visible:
    start = shot.get("start_seconds")
    stamp = f"{float(start):.1f}s" if isinstance(start, (int, float)) else "time unavailable"
    with st.expander(f"{shot_id} · {stamp} · {record.get('status', 'pending').upper()}", expanded=record.get("status") != "approved"):
        preview, detail = st.columns([1, 1.2])
        with preview:
            frames = shot.get("sample_frames") or []
            displayed = False
            for frame in frames:
                frame_path = Path(frame)
                if frame_path.is_file() and frame_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    st.image(str(frame_path), use_container_width=True)
                    displayed = True
                    break
            if not displayed:
                media_path = Path(shot.get("media_path") or "")
                if media_path.is_file() and media_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
                    st.video(str(media_path))
                else:
                    st.warning("No local visual preview is available. Restore the build artifact or replace the asset.")
        with detail:
            st.write(f"**Chapter:** {record.get('chapter_id', 'unassigned')}")
            st.write(f"**Duration:** {shot.get('duration_seconds', '—')} seconds")
            st.write(f"**Media type:** {shot.get('media_type', 'unknown')}")
            if shot.get("presenter_mode"):
                st.write(f"**Presenter mode:** {shot.get('presenter_mode')}")
                st.write(f"**Presenter A/V status:** {shot.get('presenter_sync_status') or 'not checked'}")
                if shot.get("presenter_lip_sync_verified") is True:
                    st.success("Lip-sync verified.")
                else:
                    st.warning("Phoneme-level lip-sync is not machine-verified. Inspect mouth movement against the narration.")
                if shot.get("presenter_validation"):
                    st.write("**Presenter A/V validation details**")
                    st.json(shot["presenter_validation"])
            st.write("**Narration**")
            st.write(shot.get("narration") or "Not supplied")
            st.write("**Visual prompt**")
            st.write(shot.get("visual_prompt") or "Not supplied")
            screening = shot.get("semantic_screening") or {"status": "not_run"}
            st.write("**AI visual-to-narration screening**")
            screening_status = screening.get("status", "not_run")
            if screening_status == "match":
                st.success(f"AI screen suggests a likely match (score: {screening.get('match_score', '—')}). Human approval is still required.")
            elif screening_status in {"mismatch", "uncertain", "error"}:
                st.error(f"AI screen: {screening_status}. {screening.get('recommendation') or screening.get('reason') or ''}")
            else:
                st.info(f"AI screen: {screening_status}. {screening.get('reason', 'Enable VISUAL_SEMANTIC_CHECK to run the optional vision screen.')}")
            if screening.get("visible_evidence"):
                st.caption(f"Visible evidence: {screening['visible_evidence']}")
            if screening.get("concerns"):
                st.write("**Screening concerns**")
                st.write(screening["concerns"])
            st.caption(screening.get("notice", "AI screening cannot verify truth, history, identity, rights or licensing."))
            visual_quality = shot.get("visual_quality") or {}
            st.write("**Technical visual quality**")
            quality_status = visual_quality.get("status", "unavailable")
            quality_metrics = visual_quality.get("metrics") or {}
            if quality_status == "fail":
                st.error("Technical visual-quality failure: " + "; ".join(visual_quality.get("issues") or []))
            elif quality_status == "review":
                st.warning("Visual consistency outlier: " + "; ".join(visual_quality.get("issues") or []))
            elif quality_status == "pass":
                st.success("Sampled frames passed the basic exposure, contrast and resolution checks.")
            else:
                st.info("Technical visual metrics unavailable for this shot.")
            if quality_metrics:
                st.json(quality_metrics)
            st.write("**Media source / provenance**")
            st.json(shot.get("source") or {"notice": "No source sidecar was found."})
        notes = st.text_area("Reviewer notes", value=record.get("reviewer_notes", ""), key=f"notes_{shot_id}")
        rejection = st.text_input("Rejection reason (required for rejection)", value=record.get("rejection_reason", ""), key=f"reject_{shot_id}")
        source_state = st.selectbox(
            "Source/provenance review",
            ["pending", "needs_review", "reviewed"],
            index=["pending", "needs_review", "reviewed"].index(record.get("source_review_status", "pending")),
            key=f"source_{shot_id}",
            help="This checks where the asset came from; it is separate from the rights/licence decision below.",
        )
        rights_state = st.selectbox(
            "Copyright / licence review",
            ["pending", "reviewed", "needs_permission"],
            index=["pending", "reviewed", "needs_permission"].index(record.get("rights_review_status", "pending")),
            key=f"rights_{shot_id}",
        )
        licence_name = st.text_input(
            "Licence / permission basis",
            value=record.get("licence_name", ""),
            key=f"licence_{shot_id}",
            placeholder="e.g. Pixabay Content License, CC BY 4.0, written permission, original AI-generated asset",
        )
        attribution_text = st.text_input(
            "Required attribution / credit",
            value=record.get("attribution_text", ""),
            key=f"attribution_{shot_id}",
            placeholder="Creator and credit line, or 'No attribution required' with basis",
        )
        rights_notes = st.text_area(
            "Rights review notes (required to mark reviewed)",
            value=record.get("rights_notes", ""),
            key=f"rights_notes_{shot_id}",
            placeholder="What terms did you inspect? Is commercial use allowed? Are attribution or restrictions required?",
        )
        rights_args = {
            "rights_review_status": rights_state, "rights_notes": rights_notes,
            "licence_name": licence_name, "attribution_text": attribution_text,
        }
        x1, x2, x3, x4 = st.columns(4)
        try:
            if x1.button("Approve shot", key=f"approve_{shot_id}", type="primary"):
                save_review(report_path, shot_id, status="approved", reviewer_notes=notes,
                            source_review_status=source_state, chapter_id=record.get("chapter_id"),
                            **rights_args)
                st.rerun()
            if x2.button("Reject shot", key=f"reject_btn_{shot_id}"):
                save_review(report_path, shot_id, status="rejected", rejection_reason=rejection,
                            reviewer_notes=notes, source_review_status=source_state,
                            chapter_id=record.get("chapter_id"), **rights_args)
                st.rerun()
            if x3.button("Request replacement", key=f"replace_{shot_id}"):
                save_review(report_path, shot_id, status="needs_replacement",
                            replacement_requested=True, reviewer_notes=notes,
                            source_review_status=source_state, chapter_id=record.get("chapter_id"),
                            **rights_args)
                st.rerun()
            if x4.button("Save notes / source", key=f"save_{shot_id}"):
                save_review(report_path, shot_id, reviewer_notes=notes,
                            source_review_status=source_state, chapter_id=record.get("chapter_id"),
                            **rights_args)
                st.rerun()
        except ReviewStoreError as exc:
            st.error(str(exc))

with st.expander("Publication gate details"):
    if release_issues:
        for issue in release_issues:
            st.error(f"{issue.get('shot_id', 'Review')}: {issue['message']}")
    else:
        st.success("All manifest shots, rights decisions, audio listening review and research claims have passed their required checks.")
    st.caption(gate["notice"])
st.caption(f"Decision file: {review_store_path(report_path)}")
