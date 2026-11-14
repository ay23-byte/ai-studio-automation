#!/usr/bin/env python3
"""
AI Studio - Command Line Runner
Usage:
  python run.py shorts  "Inflation kya hai"  --category finance
  python run.py long    "Mutual Fund explained" --category finance --minutes 7
  python run.py review  --id 1        (build the human-review package and dashboard)
  python run.py upload  --id 1 --decisions review_decisions.json
                                      (PRIVATE upload of the reviewed file only)
  python run.py resume  --id 1        (continue a failed/interrupted job from its checkpoints)
  python run.py status  --id 1        (stage-by-stage progress of a job)
  python run.py doctor                (check tools, fonts, providers, database and credentials)
  python run.py list
  python run.py demo    (builds a video without Gemini, using a built-in demo script)
"""
import sys, os, argparse, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()                        # reads .env if present
sys.path.insert(0, str(Path(__file__).parent))

from core.db import save_video, update_video, list_videos, get_video
from core.script_engine import generate_shorts_script, generate_longform_script, generate_seo
from core.pipeline import build_shorts, build_longform
from core.notify import send


DEMO_SHORT = {
    "title": "Inflation kya hai? Paise ki value kyun ghat jaati hai",
    "hook": "Aapke paise ki value chupchap ghat rahi hai",
    "category": "finance",
    "format": "shorts",
    "scenes": [
        {"say": "Have you noticed that the same one hundred rupees buys less today than it did five years ago?",
         "caption": "Why does the same money buy less today?",
         "label": "Inflation", "image": "a shopping basket that has become smaller while the same stack of coins sits beside it", "keywords": ["grocery"]},
        {"say": "That is inflation: the gradual rise in the prices of goods and services over time.",
         "caption": "Inflation means prices rise over time.",
         "label": "Daam badhte hain", "image": "price tags rising like steps of a staircase in warm light", "keywords": ["market"]},
        {"say": "Simply holding cash may not be enough. You also need to consider whether your savings can keep up with rising prices.",
         "caption": "Can your savings keep up with rising prices?",
         "label": "Bachat vs mehngai", "image": "a small plant standing firm beside a rising wave of water", "keywords": ["wave"]},
        {"say": "This video is for education, not investment advice. Follow for one clear concept every day.",
         "caption": "Educational content only. Follow for more.",
         "label": "", "image": "a glowing lightbulb above a neat stack of coins", "keywords": ["lightbulb"]}
    ]
}

DEMO_LONG = {
    "title": "Share Market kya hai? Beginners ke liye complete guide",
    "description": "Is video mein hum share market ke basic concepts ko aasaan Hinglish mein samjhenge.",
    "tags": ["share market", "stock market", "finance", "hinglish"],
    "hook": "Share market sun ke darta hai? Aaj sab clear ho jaayega.",
    "category": "finance",
    "format": "longform",
    "sections": [
        {"title": "Share Market kya hota hai?",
         "narration": "Socho ek company ka kaam achha chal raha hai. Wo bada hona chahti hai. Lekin uske paas paisa nahi hai. To wo kya karti hai? Wo apna kuch hissa logon ko bechti hai. Isi hisse ko share kehte hain. Jo log ye share khareedta hain, wo us company ke thode se maalik ban jaate hain.",
         "visuals": ["company building animation","people buying shares","stock exchange hall"],
         "image": "a stylized building splitting into many small glowing pieces being handed to different people",
         "keywords": ["building","office"]},
        {"title": "Share ka daam kyun badhta aur ghatta hai?",
         "narration": "Jab bahut log ek share khareedna chahte hain, to uska daam badhta hai. Aur jab log bechna chahte hain, to daam girta hai. Agar company ka kaam achha chal raha ho, company ka profit badh raha ho, to log us share mein dilchaspi dikhate hain. Isi wajah se daam badh sakta hai. Aur agar company ke results kharab ho, to daam gir sakta hai.",
         "visuals": ["seesaw animation","growing plant","falling leaves"],
         "image": "a seesaw with a glowing office building on one side and golden coins on the other",
         "keywords": ["seesaw","balance"]},
        {"title": "Sensex aur Nifty kya hai?",
         "narration": "Imagine karo ek class mein pachaas baache hain. Aap sabke number dekhne ki jagah sirf topper bachchon ke number dekhke puri class ka haal samajh lete ho. Usi tarah Nifty NSE ki pachaas badi companies ka ek group hai. Sensex BSE ki tees badi companies ka group hai. Jab in companies ke share ke daam average mein badhte hain, to index upar jaata hai.",
         "visuals": ["classroom with spotlight","thermometer","city skyline"],
         "image": "a glowing thermometer standing in front of a lit city skyline at night",
         "keywords": ["city skyline","night"]},
        {"title": "Kya share market mein risk hai?",
         "narration": "Haan, share market mein risk hota hai. Paisa badhne ki sambhavna bhi hoti hai, aur paisa kam hone ki bhi. Ye koi guarantee wali jagah nahi hai. Isiliye pehle samjho, phir sochi samjhi jagah par invest karo. Yaad rakho, ye video sirf educational information ke liye hai, investment advice nahi. Kisi bhi financial decision se pehle ek registered advisor se zaroor baat karo.",
         "visuals": ["warning sign","balance scale","advisor meeting"],
         "image": "an amber warning triangle made of light floating above a calm dark lake with no text",
         "keywords": ["lake","warning"]}
    ]
}


def _check_duplicate(kind, title, topic, force):
    """Refuse to rebuild a subject that already exists unless --force is given."""
    from core import dedupe
    duplicate = dedupe.find_duplicate(kind, title, topic=topic)
    if duplicate and not force:
        where = f" (YouTube {duplicate['youtube_id']})" if duplicate.get("youtube_id") else ""
        raise SystemExit(
            f"Duplicate topic: '{topic or title}' matches '{duplicate['match']}'{where} "
            f"[{duplicate['layer']}]. Use --force to build it anyway.")
    return duplicate


def _record_built(kind, script, topic, job_id):
    from core import dedupe
    try:
        dedupe.Ledger().record(kind, script.get("title", "") or topic, topic=topic,
                               job_id=job_id, status="built")
    except Exception as exc:
        print(f"WARNING: could not update the publish ledger: {exc}")


def cmd_shorts(args):
    from core import dedupe
    topic = "" if args.demo else str(args.topic or "")
    force = bool(getattr(args, "force", False)) or args.demo
    if args.demo:
        script = DEMO_SHORT
    else:
        _check_duplicate("shorts", topic, topic, force)
        print(f"Generating Shorts script for: {args.topic}")
        script = generate_shorts_script(args.topic, args.category, language=args.language)
        _check_duplicate("shorts", script.get("title", ""), topic, force)
    print(f"Script: {script.get('title')}")
    vid_id = save_video(script.get("title",""), "shorts", args.language, args.category, script,
                        pipeline="shorts", topic_key=dedupe.topic_key(topic or script.get("title", "")))
    print(f"Building video (job #{vid_id})...")
    try:
        out = build_shorts(script, vid_id, language=args.language, voice_gender=args.voice_gender)
    except Exception as exc:
        send(f"❌ Shorts build failed (job #{vid_id}): {str(exc)[:300]}\nResume with: python run.py resume --id {vid_id}")
        raise
    if not args.demo:
        _record_built("shorts", script, topic, vid_id)
    print(f"Done: {out}")
    send(f"✅ Shorts built: {script.get('title')}\n{out}")
    if getattr(args, "upload", False):
        _upload(vid_id)


def cmd_long(args):
    from core import dedupe
    topic = "" if args.demo else str(args.topic or "")
    force = bool(getattr(args, "force", False)) or args.demo
    if args.demo:
        script = DEMO_LONG
    else:
        _check_duplicate("longform", topic, topic, force)
        print(f"Generating long-form script for: {args.topic}")
        script = generate_longform_script(args.topic, args.category,
                                           args.minutes - 2, args.minutes, args.language)
    print(f"Script: {script.get('title')} ({len(script.get('sections',[]))} sections)")
    vid_id = save_video(script.get("title",""), "longform", args.language, args.category, script,
                        pipeline="longform", topic_key=dedupe.topic_key(topic or script.get("title", "")))
    print(f"Building long-form video (job #{vid_id})...")
    try:
        out = build_longform(script, vid_id, language=args.language, voice_gender=args.voice_gender,
                             subtitle_language=getattr(args, "subtitle_language", None))
    except Exception as exc:
        send(f"❌ Long-form build failed (job #{vid_id}): {str(exc)[:300]}\nResume with: python run.py resume --id {vid_id}")
        raise
    if not args.demo:
        _record_built("longform", script, topic, vid_id)
    print(f"Done: {out}")
    send(f"✅ Long-form built: {script.get('title')}\n{out}")
    if getattr(args, "upload", False):
        _upload(vid_id)


def cmd_resume(args):
    """Continue a failed or interrupted job; completed stages are reused."""
    from core.db import normalize_format
    row = get_video(args.id)
    if not row:
        print(f"Job #{args.id} not found"); return
    script = json.loads(row.get("script_json") or "{}")
    fmt = normalize_format(row.get("format"))
    language = row.get("language") or "english"
    update_video(args.id, attempts=int(row.get("attempts") or 0) + 1)
    print(f"Resuming job #{args.id} ({fmt}): {row.get('title')}")
    if fmt == "shorts":
        out = build_shorts(script, args.id, language=language, voice_gender=args.voice_gender)
    elif fmt == "longform":
        out = build_longform(script, args.id, language=language, voice_gender=args.voice_gender)
    else:
        raise SystemExit(f"Job #{args.id} has format '{fmt}', which this command cannot resume.")
    print(f"Done: {out}")
    send(f"✅ Resumed job #{args.id} finished: {row.get('title')}\n{out}")


def cmd_status(args):
    from core.config import BUILD_DIR
    from core.jobs import load_checkpoint_summary
    row = get_video(args.id)
    if not row:
        print(f"Job #{args.id} not found"); return
    print(f"Job #{row['id']}  {row.get('format')}  status={row.get('status')}  "
          f"quality={row.get('quality_status') or '-'}  youtube={row.get('youtube_id') or '-'}")
    print(f"Title: {row.get('title')}")
    if row.get("error"):
        print(f"Last error: {row['error']}")
    stages = load_checkpoint_summary(BUILD_DIR / f"job_{args.id}")
    if not stages:
        print("No checkpoint recorded for this job."); return
    print(f"\n{'Stage':<18}{'Status':<10}{'Attempt':>8}{'Seconds':>10}")
    for stage in stages:
        seconds = stage.get("duration_seconds")
        print(f"{stage['stage']:<18}{stage.get('status',''):<10}{stage.get('attempt',1):>8}"
              f"{(f'{seconds:.1f}' if isinstance(seconds, (int, float)) else '-'):>10}")
        if stage.get("error"):
            print(f"    error: {stage['error'][:200]}")


def _upload(job_id):
    """``--upload`` after a build: a fresh video has no human review yet, so the
    review package is prepared and the upload waits for the reviewer's decisions."""
    class _Args:
        id = job_id
    print("\nA new video cannot be uploaded before a person has reviewed it. Preparing the review package.")
    cmd_review(_Args())


def cmd_upload(args):
    """Private upload through the shared release gate and duplicate checks."""
    from core.publish import publish_job, DuplicateUpload
    from core.release_gate import ReleaseBlocked
    try:
        if getattr(args, "decisions", None):
            from core.db import get_video
            from core.local_review import ReviewUnavailable, record_decisions
            from scripts.apply_review_decisions import DecisionsRejected
            row = get_video(args.id)
            if not row:
                raise LookupError(f"Job #{args.id} was not found.")
            try:
                applied = record_decisions(row, args.decisions)
            except (DecisionsRejected, ReviewUnavailable) as exc:
                raise SystemExit(f"Review decisions refused; nothing was uploaded.\n{exc}")
            print(f"Review decisions recorded (reviewer: {applied.get('reviewer')}).")
        result = publish_job(args.id, dry_run=bool(getattr(args, "dry_run", False)))
    except LookupError as exc:
        print(exc); return
    except (ReleaseBlocked, DuplicateUpload) as exc:
        send(f"⚠️ Upload blocked for job #{args.id}: {str(exc)[:400]}")
        raise SystemExit(str(exc))
    if result.get("status") == "dry_run":
        print(json.dumps({k: result[k] for k in ("metadata", "marker")}, ensure_ascii=False, indent=2))
        print("Dry run: release gate passed; nothing was uploaded.")
    elif result.get("status") == "already_uploaded":
        print(f"Job #{args.id} is already uploaded: {result['url']}")


def cmd_review(args):
    """Build the review package of a finished job. Uploads nothing."""
    from core.db import get_video
    from core.local_review import ReviewUnavailable, build_package
    row = get_video(args.id)
    if not row:
        raise SystemExit(f"Job #{args.id} was not found.")
    try:
        summary = build_package(row)
    except ReviewUnavailable as exc:
        raise SystemExit(str(exc))
    print(f"\nReview package: {summary['package_dir']}")
    print(f"Status: {summary['status']}. Nothing was uploaded.")
    if summary["status"] != "REVIEW_REQUIRED":
        for line in summary.get("errors", []):
            print(f"  BLOCKING: {line}")
        raise SystemExit("This video fails automated QA and cannot be approved. Fix the cause and rebuild.")
    print("\nNext steps:")
    print(f"  1. Open in a browser: {summary['dashboard']}")
    print("  2. Watch the whole video and decide every scene, asset, source and QA report.")
    print("  3. Download review_decisions.json from the dashboard.")
    print(f"  4. python run.py upload --id {args.id} --decisions <path to review_decisions.json>")


def cmd_doctor(args):
    from core.health import run_health_check, print_health
    report = run_health_check()
    print_health(report)
    if report["status"] == "FAIL":
        raise SystemExit(1)


def cmd_list(args):
    rows = list_videos()
    if not rows: print("No videos yet. Run 'python run.py shorts \"your topic\"'"); return
    print(f"\n{'ID':>4}  {'Format':>8}  {'Status':>10}  Title")
    print("-"*70)
    for r in rows:
        yt = f" -> {r['youtube_id']}" if r.get("youtube_id") else ""
        print(f"{r['id']:>4}  {r['format']:>8}  {r['status']:>10}  {r['title'][:45]}{yt}")


def main():
    p = argparse.ArgumentParser(description="AI Studio - YouTube Video Creator")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("shorts", help="Create a YouTube Short")
    ps.add_argument("topic", nargs="?", default="Inflation kya hai")
    ps.add_argument("--category", default="finance")
    ps.add_argument("--language", default="english", choices=["english", "hindi", "hinglish"])
    ps.add_argument("--voice-gender", choices=["male", "female"], default="male", help="Narration voice gender")
    ps.add_argument("--demo", action="store_true", help="Use built-in demo script (no Gemini needed)")
    ps.add_argument("--force", action="store_true", help="Build even if this topic was already produced")
    ps.add_argument("--upload", action="store_true", help="After the build, prepare the review package needed for a private upload")

    pl = sub.add_parser("long", help="Create a long-form video")
    pl.add_argument("topic", nargs="?", default="How the World Works")
    pl.add_argument("--category", default="auto", help="Documentary category; use auto to infer from any topic")
    pl.add_argument("--language", default="english", choices=["english", "hindi", "hinglish"])
    pl.add_argument("--subtitle-language", default=None, choices=["english", "hindi", "hinglish", "source"],
                    help="Burned-in caption language (default: SUBTITLE_LANGUAGE or english)")
    pl.add_argument("--voice-gender", choices=["male", "female"], default="male", help="Narration voice gender")
    pl.add_argument("--minutes", type=int, choices=range(10, 26), default=12, help="Target runtime in minutes (10-25)")
    pl.add_argument("--demo", action="store_true")
    pl.add_argument("--force", action="store_true", help="Build even if this topic was already produced")
    pl.add_argument("--upload", action="store_true", help="After the build, prepare the review package needed for a private upload")

    pu = sub.add_parser("upload", help="Upload a built video to YouTube (private)")
    pu.add_argument("--id", type=int, required=True)
    pu.add_argument("--dry-run", action="store_true", help="Run the release gate and show metadata only")
    pu.add_argument("--decisions", default=None,
                    help="review_decisions.json exported from the job's review dashboard")

    pv = sub.add_parser("review", help="Build the human-review package of a finished job (no upload)")
    pv.add_argument("--id", type=int, required=True)

    pr = sub.add_parser("resume", help="Continue a failed or interrupted job from its checkpoints")
    pr.add_argument("--id", type=int, required=True)
    pr.add_argument("--voice-gender", choices=["male", "female"], default="male")

    pt = sub.add_parser("status", help="Show stage-by-stage progress of a job")
    pt.add_argument("--id", type=int, required=True)

    sub.add_parser("doctor", help="Check tools, fonts, providers, database and credentials")
    sub.add_parser("list", help="List all videos")

    pd = sub.add_parser("demo", help="Build a demo video without any API keys")
    pd.add_argument("--format", choices=["shorts","long"], default="shorts")

    args = p.parse_args()
    if args.cmd == "shorts": cmd_shorts(args)
    elif args.cmd == "long": cmd_long(args)
    elif args.cmd == "upload": cmd_upload(args)
    elif args.cmd == "review": cmd_review(args)
    elif args.cmd == "resume": cmd_resume(args)
    elif args.cmd == "status": cmd_status(args)
    elif args.cmd == "doctor": cmd_doctor(args)
    elif args.cmd == "list": cmd_list(args)
    elif args.cmd == "demo":
        class A: topic="demo"; category="finance"; language="english"; voice_gender="male"; demo=True
        a = A()
        if args.format == "shorts": a.minutes = None; cmd_shorts(a)
        else: a.minutes = 12; cmd_long(a)
    else:
        p.print_help()

if __name__ == "__main__":
    main()
