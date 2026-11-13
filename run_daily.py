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
from core import dedupe, topics as topic_queue


def _kind(fmt):
    return "longform" if fmt in ("long", "longform") else "shorts"


def load_topics():
    return topic_queue.load_topics()


def save_topics(topics):
    """Atomic, locked write (kept for backward compatibility)."""
    with topic_queue.locked(topic_queue.TOPICS_PATH):
        topic_queue.save_topics(topics)


def next_topic(fmt="shorts"):
    return topic_queue.next_topic(fmt)


def mark_done(topics, t, youtube_id=None):
    changes = {"done": True}
    if youtube_id:
        changes["youtube_id"] = youtube_id
    topic_queue.update_topic(t.get("title") or t.get("topic"), **changes)


def upload_latest(video_format=None):
    """Upload the newest finished, unuploaded job PRIVATELY through the shared gate."""
    from core.publish import publish_latest, DuplicateUpload
    from core.release_gate import ReleaseBlocked
    try:
        result = publish_latest(_kind(video_format) if video_format else None, wait=True)
    except (ReleaseBlocked, DuplicateUpload) as exc:
        # A blocked upload is a deliberate, safe outcome: keep the artifacts,
        # tell the operator, and fail the step so it is visible.
        send(f"⚠️ Upload blocked: {str(exc)[:500]}")
        raise
    if result.get("youtube_id"):
        _mark_uploaded_topic(result)
    return result


def prepare_review(job_id):
    """``--upload`` right after a build: a new video has no human review yet, so the
    review package is prepared and the upload waits for the reviewer's decisions.

    Never raises: the build succeeded, and a missing review must not be recorded
    as a failed topic.
    """
    from core.db import get_video
    from core.local_review import ReviewUnavailable, build_package
    try:
        summary = build_package(get_video(job_id))
    except ReviewUnavailable as exc:
        send(f"⚠️ Job #{job_id} was built but its review package could not be prepared: {str(exc)[:300]}")
        return None
    if summary["status"] != "REVIEW_REQUIRED":
        send(f"⚠️ Job #{job_id} fails automated QA and cannot be approved: "
             + "; ".join(summary.get("errors", [])[:3])[:400])
        return summary
    print(f"Review package ready: {summary['dashboard']}")
    send(f"📝 Job #{job_id} is ready for review. Open {summary['dashboard']}, decide every item, then run: "
         f"python run.py upload --id {job_id} --decisions review_decisions.json")
    return summary


def _mark_uploaded_topic(result):
    """Record the YouTube id on the matching queue entry, if there is one."""
    try:
        title = str(result.get("title") or "").replace(" #Shorts", "").strip()
        for item in load_topics():
            name = item.get("title") or item.get("topic") or ""
            if name and dedupe.is_same_subject(name, title):
                topic_queue.update_topic(name, youtube_id=result["youtube_id"], done=True)
                break
    except Exception as exc:
        print(f"WARNING: could not record the upload in topics.json: {exc}")


def _language_for(fmt, topic=None):
    default = (os.getenv("SHORTS_LANGUAGE", "hinglish") if _kind(fmt) == "shorts"
               else os.getenv("LONGFORM_LANGUAGE", "english"))
    value = str((topic or {}).get("language") or default).strip().lower()
    return value if value in {"english", "hindi", "hinglish"} else "english"


def _build(script, fmt, language, category, topic_text):
    """Save the job, build it, and record it in the duplicate ledger."""
    kind = _kind(fmt)
    vid_id = save_video(script.get("title", "") or topic_text, kind, language, category, script,
                        pipeline=f"daily_{kind}", topic_key=dedupe.topic_key(topic_text or script.get("title", "")))
    out = (build_shorts(script, vid_id, language=language) if kind == "shorts"
           else build_longform(script, vid_id, language=language))
    dedupe.Ledger().record(kind, script.get("title", "") or topic_text, topic=topic_text,
                           job_id=vid_id, status="built")
    return vid_id, out


def _has_script_model():
    from core import llm
    return bool(os.getenv("GEMINI_API_KEY", "").strip()) or bool(llm.configured_providers())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--format", choices=["shorts", "long"], default="shorts")
    p.add_argument("--upload-latest", action="store_true")
    p.add_argument("--upload", action="store_true",
                   help="After the build, prepare the review package needed for a private upload")
    args = p.parse_args()

    if args.upload_latest:
        upload_latest(args.format)
        return

    kind = _kind(args.format)
    # Build the next approved, non-duplicate topic from the queue.
    topics, t = next_topic(args.format)
    if not t:
        if not _has_script_model():
            send("⚠️ No unused topics in topics.json and no script model key is set "
                 "(GEMINI_API_KEY or a fallback provider).")
            return
        language = _language_for(args.format)
        print("No pending topics in topics.json. Selecting a fresh, non-duplicate topic...")
        candidate = topic_queue.pick_fresh_topic(args.format, language=language)
        if not candidate:
            send("⚠️ Every proposed and curated topic has already been produced. "
                 "Add new topics to topics.json or config/topic_bank.json.")
            return
        topic_title, category = candidate["topic"], candidate.get("category", "physics")
        print(f"Selected topic ({candidate.get('source')}): {topic_title} [{category}]")
        try:
            if kind == "shorts":
                script = generate_shorts_script(topic_title, category, language=language)
            else:
                script = generate_longform_script(topic_title, category, language=language)
            # The generated title can still collide with something already made.
            clash = dedupe.find_duplicate(kind, script.get("title", ""), topic=topic_title)
            if clash:
                send(f"⚠️ Generated script '{script.get('title')}' duplicates '{clash['match']}'; skipped.")
                dedupe.Ledger().record(kind, topic_title, topic=topic_title, status="skipped_duplicate")
                return
            vid_id, out = _build(script, args.format, language, category, topic_title)
            print(f"Built auto-generated video: {out}")
            if args.upload:
                prepare_review(vid_id)
            return
        except Exception as e:
            send(f"❌ Auto-generate failed for '{topic_title}': {str(e)[:300]}")
            raise

    title = t.get("title") or t.get("topic", "")
    print(f"Building: {title}")
    try:
        language = _language_for(args.format, t)
        category = t.get("category", "physics")
        # If the topic has a pre-written script (from topics.json)
        if t.get("scenes") or t.get("sections"):
            script = t
        elif _has_script_model():
            topic_text = t.get("title") or t.get("topic", "")
            if kind == "shorts":
                script = generate_shorts_script(topic_text, category, language=language)
            else:
                script = generate_longform_script(topic_text, category, language=language)
        else:
            send("❌ Topic has no script and no script model key is set.")
            return

        vid_id, out = _build(script, args.format, language, category, t.get("topic") or title)
        mark_done(topics, t)
        print(f"Built: {out}")
        remaining = topic_queue.remaining()
        if remaining <= 3:
            send(f"⚠️ Only {remaining} topics left in topics.json. "
                 "New topics will be selected automatically; add your own to steer the channel.")
        if args.upload:
            prepare_review(vid_id)
    except Exception as e:
        attempts = int(t.get("attempts", 0) or 0) + 1
        changes = {"attempts": attempts, "last_error": str(e)[:300]}
        if attempts >= 3:
            changes["skipped"] = True
            changes["skip_reason"] = "failed three times"
        topic_queue.update_topic(title, **changes)
        send(f"❌ Build failed for '{title}': {str(e)[:300]}")
        raise


if __name__ == "__main__":
    main()
