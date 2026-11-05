"""YouTube SEO Studio — editable metadata workspace."""
import json
import streamlit as st
from core.db import list_videos, update_video

st.set_page_config(page_title="YouTube SEO Studio", page_icon="🔎", layout="wide")
st.title("🔎 YouTube SEO Studio")
st.caption("Generate and edit titles, descriptions, tags, hashtags, chapter timestamps, pinned comments and thumbnail text.")

rows = list_videos(limit=200)
if not rows:
    st.info("Create a script or video job first.")
    st.stop()

labels = [f"#{r['id']} · {r.get('title') or 'Untitled'} · {r.get('format') or 'video'}" for r in rows]
selected = st.selectbox("Choose a video", labels)
item = rows[labels.index(selected)]
try:
    doc = json.loads(item.get("script_json") or "{}")
except (TypeError, json.JSONDecodeError):
    doc = {}

package = doc.get("seo") if isinstance(doc.get("seo"), dict) else {}
if st.button("✨ Generate SEO package", type="primary"):
    try:
        from core.seo import generate_seo_package
        with st.spinner("Generating metadata with your configured Gemini model..."):
            package = generate_seo_package(doc, item.get("channel_name") or "")
        doc["seo"] = package
        update_video(item["id"], script_json=json.dumps(doc, ensure_ascii=False))
        st.success("SEO package generated and saved.")
        st.rerun()
    except Exception as exc:
        st.error(f"SEO generation failed: {exc}")

if package:
    from core.seo import normalize_seo_package
    titles = package.get("titles") or [item.get("title") or "Untitled"]
    chosen = st.selectbox("Title options (max 100 characters)", titles)
    description = st.text_area("Description (max 5,000 characters)", package.get("description", ""), height=180)
    tags = st.text_area("Tags (comma-separated; max 30 / 500 characters)", ", ".join(package.get("tags", [])))
    hashtags = st.text_input("Hashtags (max 3)", " ".join(package.get("hashtags", [])))
    pinned = st.text_area("Pinned comment", package.get("pinned_comment", ""), height=90)
    thumb = st.text_input("Thumbnail text options (separate with |)", " | ".join(package.get("thumbnail_text", [])))
    chapter_text = "\n".join(f"{x['timestamp']} {x['title']}" for x in package.get("chapters", []))
    chapters = st.text_area("Chapter timestamps — verify against the final video", chapter_text, height=140)
    st.caption(package.get("chapters_notice", ""))
    st.caption(package.get("keyword_research_note", ""))
    if st.button("💾 Save SEO edits"):
        package.update({
            "titles": titles, "description": description,
            "tags": [x.strip() for x in tags.split(",") if x.strip()],
            "hashtags": [x.strip() for x in hashtags.split() if x.strip()],
            "pinned_comment": pinned,
            "thumbnail_text": [x.strip() for x in thumb.split("|") if x.strip()],
            "chapters": [{"timestamp": p[0], "title": p[1]} for line in chapters.splitlines() if len(p := line.strip().split(maxsplit=1)) == 2],
        })
        package = normalize_seo_package(package)
        doc["seo"] = package
        # Keep legacy upload metadata in sync with the approved SEO copy.
        doc["description"] = package["description"]
        doc["tags"] = package["tags"]
        doc["seo_selected_title"] = chosen[:100]
        update_video(item["id"], title=chosen[:100], script_json=json.dumps(doc, ensure_ascii=False))
        st.success("SEO metadata saved. The existing upload flow will use the saved title, description and tags.")
        st.rerun()
    st.markdown("**Copy-ready description**")
    st.code(description, language="text")
    if package.get("chapters"):
        st.markdown("**Chapter preview**")
        st.code("\n".join(f"{x['timestamp']} {x['title']}" for x in package["chapters"]), language="text")
else:
    st.info("Generate an SEO package to create editable metadata for this video.")

st.warning("AI-generated keyword suggestions are not live search-volume or trend data. Long-form chapter times are estimates based on narration length and the script runtime; verify them against the rendered video before publishing. Shorts do not use chapters.")
