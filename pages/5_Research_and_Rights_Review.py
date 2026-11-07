"""Human source and claim verification for documentary research."""
import json
from pathlib import Path

import streamlit as st

from core.config import BUILD_DIR
from core.research_verification import (
    audit_research_reviews, load_research_reviews, save_claim_review,
    save_coverage_review, save_source_review,
)

st.title("📚 Research, citations & rights verification")
st.caption("Source links and AI-supplied evidence are leads, not proof. Open original documents and verify each claim before publication.")

reports = sorted(
    BUILD_DIR.rglob("research_verification_report.json"),
    key=lambda path: path.stat().st_mtime, reverse=True,
)
if not reports:
    st.info("No research verification reports found. Generate a new long-form documentary with the claim-ledger prompt.")
    st.stop()

labels = [f"{path.parent.name} · {path}" for path in reports]
selected = st.selectbox("Documentary build", labels)
report_path = reports[labels.index(selected)]
build_dir = report_path.parent
try:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    store = load_research_reviews(build_dir, report)
except Exception as exc:
    st.error(f"Cannot load research verification data: {exc}")
    st.stop()

gate = audit_research_reviews(report, store)
a, b, c = st.columns(3)
a.metric("Sources", gate["source_count"])
b.metric("Claims verified", f"{gate['verified_claim_count']} / {gate['claim_count']}")
c.metric("Sources reviewed", f"{gate['reviewed_source_count']} / {gate['source_count']}")
if gate["publication_ready"]:
    st.success("Research review gate passed. This is a human editorial attestation, not legal or scientific certification.")
else:
    st.error(f"Research publication gate blocked: {len(gate['issues'])} issue(s) remain.")

st.warning(
    "Important: a valid URL, a GDELT discovery match, or an AI-generated evidence note does not prove that a source supports a claim. "
    "Open the original source, inspect the relevant passage/table/data, check publication date and context, and record your own notes."
)

with st.expander("Source register", expanded=True):
    for source in report.get("sources") or []:
        sid = source["source_id"]
        decision = (store.get("sources") or {}).get(sid, {})
        with st.container(border=True):
            st.markdown(f"**{sid} — {source.get('title') or 'Untitled source'}**")
            st.write(f"Publisher: {source.get('publisher') or 'Unknown'} · Published: {source.get('published_at') or 'Unknown'}")
            if source.get("url_valid"):
                st.markdown(f"[Open original source]({source['url']})")
            else:
                st.error("Invalid or missing source URL.")
            if source.get("matched_discovery_lead"):
                st.caption("Matched a retrieved news-discovery lead only; source contents still need inspection.")
            else:
                st.caption("Not in the retrieved discovery leads; independently validate the publisher and URL.")
            if source.get("supports"):
                st.write("Claim support suggested by script model:")
                st.write(source["supports"])
            states = ["pending", "reviewed", "needs_correction"]
            status = st.selectbox(
                "Source review status", states,
                index=states.index(decision.get("status", "pending")),
                key=f"research_source_status_{sid}",
            )
            notes = st.text_area(
                "Your source inspection notes", value=decision.get("reviewer_notes", ""),
                key=f"research_source_notes_{sid}",
                placeholder="Describe the original passage/data checked, date, context, limitations, and whether the source is suitable.",
            )
            if st.button("Save source review", key=f"save_research_source_{sid}"):
                try:
                    save_source_review(build_dir, report, sid, status, notes)
                    st.success(f"Saved review for {sid}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

with st.expander("Claim ledger", expanded=True):
    for claim in report.get("claims") or []:
        cid = claim["claim_id"]
        decision = (store.get("claims") or {}).get(cid, {})
        with st.container(border=True):
            st.markdown(f"**{cid} — {claim.get('claim_type', 'factual').replace('_', ' ').title()}**")
            st.write(claim.get("text") or "Claim text missing.")
            st.write(f"Evidence note supplied with script: {claim.get('evidence') or 'No evidence note supplied.'}")
            st.write(f"Source IDs: {', '.join(claim.get('source_ids') or []) or 'None'}")
            for sid in claim.get("valid_source_ids") or []:
                source = next((row for row in report.get("sources", []) if row["source_id"] == sid), {})
                if source.get("url_valid"):
                    st.markdown(f"[{sid}: {source.get('title') or 'Open source'}]({source['url']})")
            states = ["pending", "verified", "disputed", "unsupported"]
            status = st.selectbox(
                "Claim decision", states,
                index=states.index(decision.get("status", "pending")),
                key=f"research_claim_status_{cid}",
            )
            notes = st.text_area(
                "Your evidence-based verification notes", value=decision.get("reviewer_notes", ""),
                key=f"research_claim_notes_{cid}",
                placeholder="What exact source passage/table supports this? Note contrary evidence, dates, units and limitations.",
            )
            if st.button("Save claim decision", key=f"save_research_claim_{cid}"):
                try:
                    save_claim_review(build_dir, report, cid, status, notes)
                    st.success(f"Saved decision for {cid}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

st.subheader("Claim-ledger completeness")
st.write(
    "Review the complete narration—not just the generated claim list—and confirm every material factual, numeric, "
    "historical, causal or attributed assertion is represented. Add missing claims to the source script/report before marking complete."
)
coverage = st.checkbox(
    "I checked the complete narration and confirmed that all material factual claims are represented in this claim ledger.",
    value=bool(store.get("coverage_reviewed")),
    key=f"coverage_reviewed_{build_dir.name}",
)
coverage_notes = st.text_area(
    "Coverage review notes", value=store.get("coverage_notes", ""),
    key=f"coverage_notes_{build_dir.name}",
    placeholder="Describe how you checked the narration for uncited claims and handled any unsupported or disputed statements.",
)
if st.button("Save claim-ledger completeness review", key=f"save_coverage_{build_dir.name}"):
    try:
        save_coverage_review(build_dir, report, coverage, coverage_notes)
        st.success("Claim-ledger completeness decision saved.")
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

with st.expander("Publication gate details"):
    if gate["issues"]:
        for issue in gate["issues"]:
            st.error(f"{issue.get('claim_id') or issue.get('source_id') or 'Research'}: {issue['message']}")
    else:
        st.success("Every source and claim has a human review decision, and claim-ledger coverage has been attested.")
    st.caption(gate["notice"])
st.caption(f"Decision file: {build_dir / 'research_review_decisions.json'}")
