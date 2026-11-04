#!/usr/bin/env python3
"""Daily runner for GitHub Actions.
- Picks the next unused topic from topics.json
- Builds the video
- Optionally uploads to YouTube
"""
import sys, os, json, argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from core.db import save_video, update_video, list_videos, get_conn
from core.script_engine import generate_shorts_script, generate_longform_script, generate_seo
from core.pipeline import build_shorts, build_longform
from core.notify import send


def load_topics():
    p = Path("topics.json")
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save_topics(topics):
    Path("topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")


def next_topic(fmt="shorts"):
    topics = load_topics()
    for t in topics:
        if t.get("done") or t.get("skipped"):
            continue
        if t.get("approved") is False:
            continue
        if t.get("format", fmt) == fmt or not t.get("format"):
            return topics, t
    return topics, None


def mark_done(topics, t, youtube_id=None):
    for item in topics:
        if item.get("title") == t.get("title"):
            item["done"] = True
            if youtube_id:
                item["youtube_id"] = youtube_id
    save_topics(topics)


def upload_latest():
    rows = list_videos(limit=5)
    for row in rows:
        if row.get("video_path") and not row.get("youtube_id"):
            vpath = Path(row["video_path"])
            if vpath.exists():
                from core.uploader import upload, add_to_playlist, post_comment, wait_processed
                from core.script_engine import generate_seo
                script = json.loads(row.get("script_json") or "{}")
                seo = generate_seo(script)
                title = row["title"]
                if row.get("format") == "shorts":
                    title = title[:88] + " #Shorts"
                desc = script.get("description", "Engineering Physics - learn one concept every day.")
                cat = row.get("category", "")
                if cat in ("finance", "markets", "money"):
                    desc += ("\n\nयह वीडियो सिर्फ़ शैक्षणिक जानकारी के लिए है।"
                             " यह निवेश सलाह नहीं है।")
                desc += "\n\n#Shorts" if row.get("format") == "shorts" else ""
                print(f"Uploading: {title}")
                try:
                    vid = upload(vpath, title, desc, seo["tags"],
                                 contains_synthetic=True)
                    update_video(row["id"], youtube_id=vid, status="uploaded")
                    state = wait_processed(vid)
                    if state == "failed":
                        send(f"❌ YouTube processing failed for: {title}")
                        return
                    playlist = {
                        "finance": "Finance Explained | Engineering Physics",
                        "markets": "Share Market | Engineering Physics",
                        "money": "Paise ki Samajh | Engineering Physics",
                        "power": "पावर के नियम | Engineering Physics",
                        "human": "इंसानी व्यवहार | Engineering Physics",
                    }.get(cat, "Engineering Physics")
                    try:
                        add_to_playlist(vid, playlist)
                    except Exception as e:
                        print(f"Playlist: {e}")
                    try:
                        post_comment(vid, seo["pinned_comment"])
                    except Exception as e:
                        print(f"Comment: {e}")
                    link = (f"https://youtube.com/shorts/{vid}"
                            if row.get("format") == "shorts"
                            else f"https://youtube.com/watch?v={vid}")
                    send(f"✅ Uploaded: {title}\n{link}\nTip: pin your comment in Studio.")
                    print(f"Uploaded: {link}")
                except Exception as e:
                    send(f"❌ Upload failed: {str(e)[:200]}")
                    raise
                return
    print("No built-but-unuploaded video found.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--format", choices=["shorts", "long"], default="shorts")
    p.add_argument("--upload-latest", action="store_true")
    args = p.parse_args()

    if args.upload_latest:
        upload_latest()
        return

    # Build the next topic
    topics, t = next_topic(args.format)
    if not t:
        # Try Gemini if no pending topic
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            print("No pending topics. Generating a new one with Gemini...")
            fallback_topics = [
                ("Compound interest kya hai", "finance"),
                ("Bull aur Bear market kya hota hai", "markets"),
                ("CIBIL score kaise improve karen", "money"),
                ("Risk aur return ka rishta", "markets"),
                ("Emergency fund kyun zaroori hai", "money"),
            ]
            # pick one not already in topics.json
            existing = {t2.get("title","") for t2 in topics}
            for topic_title, category in fallback_topics:
                if topic_title not in existing:
                    try:
                        if args.format == "shorts":
                            script = generate_shorts_script(topic_title, category)
                        else:
                            script = generate_longform_script(topic_title, category)
                        vid_id = save_video(script.get("title", topic_title),
                                            args.format, "hindi", category, script)
                        if args.format == "shorts":
                            out = build_shorts(script, vid_id)
                        else:
                            out = build_longform(script, vid_id)
                        print(f"Built auto-generated video: {out}")
                        return
                    except Exception as e:
                        send(f"❌ Auto-generate failed: {str(e)[:200]}")
                        raise
            send("⚠️ All fallback topics already used. Add new topics to topics.json.")
        else:
            send("⚠️ No unused topics in topics.json and no GEMINI_API_KEY set.")
        return

    print(f"Building: {t.get('title', t.get('topic',''))}")
    try:
        # If the topic has a pre-written script (from topics.json)
        if t.get("scenes") or t.get("sections"):
            script = t
        elif os.getenv("GEMINI_API_KEY"):
            topic_text = t.get("title") or t.get("topic", "")
            category = t.get("category", "finance")
            if args.format == "shorts":
                script = generate_shorts_script(topic_text, category)
            else:
                script = generate_longform_script(topic_text, category)
        else:
            send("❌ Topic has no script and GEMINI_API_KEY is not set.")
            return

        vid_id = save_video(script.get("title", ""), args.format,
                            t.get("language","hindi"), t.get("category","finance"), script)
        if args.format == "shorts":
            out = build_shorts(script, vid_id)
        else:
            out = build_longform(script, vid_id)

        mark_done(topics, t)
        print(f"Built: {out}")
        remaining = sum(1 for x in load_topics()
                        if not x.get("done") and not x.get("skipped"))
        if remaining <= 3:
            send(f"⚠️ Only {remaining} topics left in topics.json. Add more soon.")
    except Exception as e:
        t["attempts"] = t.get("attempts", 0) + 1
        if t["attempts"] >= 3:
            t["skipped"] = True
        save_topics(topics)
        send(f"❌ Build failed for '{t.get('title','')}': {str(e)[:200]}")
        raise


if __name__ == "__main__":
    main()
