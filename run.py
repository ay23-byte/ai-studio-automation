#!/usr/bin/env python3
"""
AI Studio - Command Line Runner
Usage:
  python run.py shorts  "Inflation kya hai"  --category finance
  python run.py long    "Mutual Fund explained" --category finance --minutes 7
  python run.py upload  --id 1
  python run.py list
  python run.py demo    (builds a video without Gemini, using a built-in demo script)
"""
import sys, os, argparse, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()                        # reads .env if present
sys.path.insert(0, str(Path(__file__).parent))

from core.db import save_video, update_video, list_videos
from core.script_engine import generate_shorts_script, generate_longform_script, generate_seo
from core.pipeline import build_shorts, build_longform
from core.notify import send


DEMO_SHORT = {
    "title": "Inflation kya hai? Paise ki value kyun ghat jaati hai",
    "hook": "Aapke paise ki value chupchap ghat rahi hai",
    "category": "finance",
    "format": "shorts",
    "scenes": [
        {"say": "क्या आपने कभी सोचा है कि पाँच साल पहले सौ रुपये में जो मिलता था, आज वो उतने में क्यों नहीं मिलता?",
         "caption": "Kya 5 saal pehle 100 rupaye mein jo milta tha, aaj wo kyun nahi milta?",
         "label": "Inflation", "image": "a shopping basket that has become smaller while the same stack of coins sits beside it", "keywords": ["grocery"]},
        {"say": "इसी को इन्फ्लेशन कहते हैं। यानी समय के साथ चीज़ों के दाम धीरे धीरे बढ़ते जाना।",
         "caption": "Isi ko inflation kehte hain. Samay ke saath cheezon ke daam badhte jaate hain.",
         "label": "Daam badhte hain", "image": "price tags rising like steps of a staircase in warm light", "keywords": ["market"]},
        {"say": "इसलिए सिर्फ़ पैसे रखना काफ़ी नहीं। ये भी सोचना ज़रूरी है कि आपकी बचत महँगाई के सामने टिक पा रही है या नहीं।",
         "caption": "Sirf paise rakhna kaafi nahi. Ye bhi sochna zaroori hai ki bachat mehngai ke saamne tik pa rahi hai ya nahi.",
         "label": "Bachat vs mehngai", "image": "a small plant standing firm beside a rising wave of water", "keywords": ["wave"]},
        {"say": "ये सिर्फ़ शैक्षणिक जानकारी है। निवेश सलाह नहीं। रोज़ एक नया कॉन्सेप्ट समझने के लिए फॉलो करो।",
         "caption": "Ye sirf educational jaankari hai, investment salah nahi. Roz ek naya concept ke liye follow karo.",
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


def cmd_shorts(args):
    if args.demo:
        script = DEMO_SHORT
    else:
        print(f"Generating Shorts script for: {args.topic}")
        script = generate_shorts_script(args.topic, args.category)
    print(f"Script: {script.get('title')}")
    vid_id = save_video(script.get("title",""), "shorts", args.language, args.category, script)
    print(f"Building video (job #{vid_id})...")
    out = build_shorts(script, vid_id, language=args.language, voice_gender=args.voice_gender)
    print(f"Done: {out}")
    send(f"✅ Shorts built: {script.get('title')}\n{out}")


def cmd_long(args):
    if args.demo:
        script = DEMO_LONG
    else:
        print(f"Generating long-form script for: {args.topic}")
        script = generate_longform_script(args.topic, args.category,
                                           args.minutes - 2, args.minutes, args.language)
    print(f"Script: {script.get('title')} ({len(script.get('sections',[]))} sections)")
    vid_id = save_video(script.get("title",""), "longform", args.language, args.category, script)
    print(f"Building long-form video (job #{vid_id})...")
    out = build_longform(script, vid_id, language=args.language, voice_gender=args.voice_gender)
    print(f"Done: {out}")
    send(f"✅ Long-form built: {script.get('title')}\n{out}")


def cmd_upload(args):
    from core.uploader import upload, add_to_playlist, post_comment
    from core.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id=?", (args.id,)).fetchone()
    conn.close()
    if not row: print(f"Job #{args.id} not found"); return
    row = dict(row)
    script = json.loads(row.get("script_json") or "{}")
    seo = generate_seo(script)
    title = row.get("title","") + " #Shorts" if row.get("format")=="shorts" else row.get("title","")
    desc = script.get("description","Power Codex - roz ek naya concept.")
    print(f"Uploading: {title}")
    vid = upload(Path(row["video_path"]), title, desc, seo["tags"])
    update_video(args.id, youtube_id=vid, status="uploaded")
    cat_info = {"finance":"Finance Explained | Power Codex", "markets":"Share Market | Power Codex"}
    playlist = cat_info.get(row.get("category",""), "Power Codex")
    try: add_to_playlist(vid, playlist)
    except Exception as e: print(f"Playlist: {e}")
    try: post_comment(vid, seo["pinned_comment"])
    except Exception as e: print(f"Comment: {e}")
    print(f"Uploaded: https://youtube.com/shorts/{vid}" if row.get("format")=="shorts"
          else f"Uploaded: https://youtube.com/watch?v={vid}")
    send(f"✅ Uploaded: {title}\nhttps://youtube.com/watch?v={vid}")


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
    ps.add_argument("--language", default="hindi")
    ps.add_argument("--voice-gender", choices=["male", "female"], default="male", help="Narration voice gender")
    ps.add_argument("--demo", action="store_true", help="Use built-in demo script (no Gemini needed)")

    pl = sub.add_parser("long", help="Create a long-form video")
    pl.add_argument("topic", nargs="?", default="Share Market explained")
    pl.add_argument("--category", default="finance")
    pl.add_argument("--language", default="hindi")
    pl.add_argument("--voice-gender", choices=["male", "female"], default="male", help="Narration voice gender")
    pl.add_argument("--minutes", type=int, default=7)
    pl.add_argument("--demo", action="store_true")

    pu = sub.add_parser("upload", help="Upload a built video to YouTube")
    pu.add_argument("--id", type=int, required=True)

    sub.add_parser("list", help="List all videos")

    pd = sub.add_parser("demo", help="Build a demo video without any API keys")
    pd.add_argument("--format", choices=["shorts","long"], default="shorts")

    args = p.parse_args()
    if args.cmd == "shorts": cmd_shorts(args)
    elif args.cmd == "long": cmd_long(args)
    elif args.cmd == "upload": cmd_upload(args)
    elif args.cmd == "list": cmd_list(args)
    elif args.cmd == "demo":
        class A: topic="demo"; category="finance"; language="hindi"; demo=True
        a = A()
        if args.format == "shorts": a.minutes = None; cmd_shorts(a)
        else: a.minutes = 7; cmd_long(a)
    else:
        p.print_help()

if __name__ == "__main__":
    main()
