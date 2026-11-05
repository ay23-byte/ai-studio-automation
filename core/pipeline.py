"""Main pipeline: takes a script dict, builds a video, returns output path."""
import random, re, subprocess
from pathlib import Path
from core import config as cfg
from core.voice import synthesise
from core.images import generate_image, fetch_clip
from core.db import save_video, update_video

W_S, H_S = 1080, 1920   # Shorts
W_L, H_L = 1920, 1080   # Long-form
FADE = 0.3
TAIL = 0.6

def _pick_music():
    files = list(cfg.MUSIC_DIR.glob("*.mp3")) + list(cfg.MUSIC_DIR.glob("*.wav"))
    return random.choice(files) if files else None

def _local_clips():
    files = [p for p in cfg.CLIPS_DIR.iterdir() if p.suffix.lower() in (".mp4",".mov",".webm")]
    random.shuffle(files); return files

def _ts(s):
    cs = int(round(max(s,0)*100))
    return f"{cs//360000}:{(cs//6000)%60:02d}:{(cs//100)%60:02d}.{cs%100:02d}"

def _clean(s): return s.replace("{","(").replace("}",")")

def _caption_style(preset, is_shorts):
    if is_shorts:
        styles = {
            "clean": "Style: Cap,Noto Sans Devanagari,82,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,70,70,620,1",
            "karaoke": "Style: Cap,Noto Sans Devanagari,104,&H0000FFFF,&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,1,10,4,2,70,70,640,1",
            "bold": "Style: Cap,Noto Sans Devanagari,96,&H0000FFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,9,3,2,70,70,640,1",
        }
    else:
        styles = {
            "clean": "Style: Cap,Noto Sans Devanagari,52,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,0,0,0,0,100,100,0,0,1,3,1,2,120,120,60,1",
            "karaoke": "Style: Cap,Noto Sans Devanagari,66,&H0000FFFF,&H000000FF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,1,7,3,2,120,120,60,1",
            "bold": "Style: Cap,Noto Sans Devanagari,58,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,0,0,0,0,100,100,0,0,1,5,2,2,120,120,60,1",
        }
    return styles.get(preset, styles["bold"])


def _make_ass_shorts(items, hook, cta, total, labels, path, disclaimer):
    T = total + TAIL
    ch_name = cfg.CHANNEL_NAME
    cap_style = _caption_style(cfg.SUBTITLE_PRESET, True)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W_S}
PlayResY: {H_S}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{cap_style}
Style: Hook,Noto Sans Devanagari,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,8,3,8,90,90,300,1
Style: Label,Noto Sans,52,&H0000D7FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,3,0,1,6,2,8,80,80,175,1
Style: End,Noto Sans Devanagari,68,&H0000A5FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,80,80,1000,1
Style: Mark,Noto Sans,38,&H66FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,2,0,7,50,50,90,1
Style: Disc,Noto Sans,32,&H99FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,0,2,60,60,310,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [f"Dialogue: 0,{_ts(0)},{_ts(T)},Mark,,0,0,0,,{ch_name}"]
    if disclaimer:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(T)},Disc,,0,0,0,,Sirf educational jaankari  |  Investment advice nahi")
    if hook:
        he = min(3.2, (items[0][1]+0.3) if items else 3.0)
        lines.append(f"Dialogue: 1,{_ts(0.1)},{_ts(he)},Hook,,0,0,0,,{{\\fad(250,350)}}{_clean(hook)}")
    for st, en, lbl in (labels or []):
        if lbl:
            lines.append(f"Dialogue: 1,{_ts(st+0.1)},{_ts(en-0.1)},Label,,0,0,0,,{{\\fad(220,220)}}{lbl}")
    for st, en, text in items:
        words = _clean(text).split()
        chunks = [" ".join(words[i:i+2]) for i in range(0, len(words), 2)]
        weights = [max(len(part), 1) for part in chunks]
        total_weight = sum(weights)
        cursor = st
        span = max(en-st, 0.2)
        for part, weight in zip(chunks, weights):
            duration = span * weight / total_weight
            lines.append(f"Dialogue: 0,{_ts(cursor)},{_ts(cursor+duration)},Cap,,0,0,0,,{{\\fscx85\\fscy85\\t(0,110,\\fscx100\\fscy100)}}{part}")
            cursor += duration
    if cta:
        lines.append(f"Dialogue: 1,{_ts(max(total-1.4,0))},{_ts(T)},End,,0,0,0,,{{\\fad(300,200)}}{_clean(cta)}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _make_ass_longform(timings, sections, total, path, disclaimer):
    T = total + 1.5
    cap_style = _caption_style(cfg.SUBTITLE_PRESET, False)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W_L}
PlayResY: {H_L}
WrapStyle: 1

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{cap_style}
Style: Lower,Noto Sans,46,&H00FFFFFF,&H000000FF,&H000000FF,&HC0000014,1,0,0,0,100,100,2,0,1,3,2,7,80,80,80,1
Style: Mark,Noto Sans,30,&H66FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,40,40,40,1
Style: Disc,Noto Sans,32,&H99FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,9,80,80,40,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines = [f"Dialogue: 0,{_ts(0)},{_ts(T)},Mark,,0,0,0,,{cfg.CHANNEL_NAME}"]
    if disclaimer:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(T)},Disc,,0,0,0,,Sirf educational jaankari  |  Investment advice nahi")
    for sec in sections:
        st = sec.get("_start", 0)
        label = sec.get("title", "")[:40]
        if label:
            lines.append(f"Dialogue: 1,{_ts(st+0.2)},{_ts(st+3.5)},Lower,,0,0,0,,{{\\fad(300,400)}}{label}")
    for st, en, text in timings:
        words = _clean(text).split()
        chunks = [" ".join(words[i:i+3]) for i in range(0, len(words), 3)]
        weights = [max(len(part), 1) for part in chunks]
        total_weight = sum(weights)
        cursor = st
        span = max(en-st, 0.2)
        for part, weight in zip(chunks, weights):
            duration = span * weight / total_weight
            lines.append(f"Dialogue: 0,{_ts(cursor)},{_ts(cursor+duration)},Cap,,0,0,0,,{part}")
            cursor += duration
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _visual_beats_for_section(section):
    raw = section.get("visuals") or [section.get("image", "documentary establishing shot")]
    beats = []
    for item in raw[:6]:
        if isinstance(item, dict):
            prompt = str(item.get("visual_prompt") or item.get("prompt") or item.get("description") or "").strip()
            anchor = str(item.get("narration_anchor") or item.get("anchor") or "").strip()
            keywords = item.get("keywords") or section.get("keywords") or []
        else:
            prompt, anchor, keywords = str(item).strip(), "", section.get("keywords") or []
        if prompt:
            if isinstance(keywords, str): keywords = [keywords]
            beats.append({"prompt":prompt,"anchor":anchor,
                          "keywords":[str(k).strip() for k in keywords if str(k).strip()][:5]})
    return beats or [{"prompt":str(section.get("image") or section.get("title") or "documentary establishing shot"),
                      "anchor":"","keywords":section.get("keywords") or ["documentary"]}]


def _visual_beat_durations(narration, beats, section_duration):
    import re
    words = list(re.finditer(r"\S+", narration or ""))
    count = len(words)
    if not count or len(beats) == 1:
        return [section_duration / len(beats)] * len(beats)
    positions, search_from = [], 0
    for beat in beats:
        anchor = beat.get("anchor", "").strip()
        pos = (narration or "").casefold().find(anchor.casefold(), search_from) if anchor else -1
        if pos < 0: positions.append(None)
        else:
            positions.append(min(sum(1 for match in words if match.start() < pos), count - 1))
            search_from = pos + len(anchor)
    if any(pos is None for pos in positions) or positions != sorted(positions):
        positions = [round(i * count / len(beats)) for i in range(len(beats))]
    else: positions[0] = 0
    positions = [max(0, min(int(pos), count - 1)) for pos in positions]
    durations = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else count
        durations.append(section_duration * max(1, end-start) / count)
    durations[-1] += section_duration - sum(durations)
    return durations


def _quality_check(job_id, video_path, subtitle_path, build_dir):
    """Run free local post-render checks without discarding a usable export."""
    try:
        from core.quality import inspect_video, write_report
        report = inspect_video(video_path, subtitle_path=subtitle_path)
        report_path = write_report(report, Path(build_dir) / "quality_report.json")
        update_video(job_id, quality_report_path=str(report_path),
                     quality_score=report["score"], quality_status=report["status"])
        print(f"Video quality check: {report['status']} ({report['score']}/100)")
        for warning in report.get("warnings", []):
            print(f"QUALITY WARNING: {warning}")
        for issue in report.get("issues", []):
            print(f"QUALITY ERROR: {issue}")
    except Exception as exc:
        print(f"Video quality inspection could not complete: {exc}")


def build_shorts(script: dict, job_id: int, build_dir: Path = None, language: str = "hindi", voice_gender: str = "male") -> Path:
    """Build a 9:16 Shorts video from a scene-based script."""
    from core.editor_shorts import render as shorts_render
    bd = build_dir or (cfg.BUILD_DIR / f"job_{job_id}")
    bd.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes", [])
    if not scenes:
        raise ValueError("Shorts script must contain at least one scene.")
    if any(not str(sc.get("say", "")).strip() for sc in scenes):
        raise ValueError("Every Shorts scene must contain non-empty narration in its say field.")
    cat = script.get("category", "")
    disclaimer = cat in ("finance", "markets", "money")

    # Voice
    full_text = " ".join(sc.get("say","") for sc in scenes)
    voice_path, timings = synthesise(full_text, bd, language=language, gender=voice_gender)
    total = float(subprocess.check_output(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(voice_path)]
    ).strip())

    # Scene timing
    n = len(scenes)
    used_ids = set()
    T = total + TAIL
    lens = [T / n] * n
    cuts = [k * T / n for k in range(1, n)]

    # Visuals
    media, local = [], _local_clips()
    for i, sc in enumerate(scenes):
        img_p = bd / f"img{i}.jpg"
        if generate_image(sc.get("image","cinematic abstract"), img_p):
            media.append({"type":"image","path":img_p})
        else:
            clip_p = bd / f"raw{i}.mp4"
            kws = sc.get("keywords", ["city"])
            if fetch_clip(kws, clip_p, used_ids):
                media.append({"type":"video","path":clip_p})
            elif local:
                media.append({"type":"video","path":local[i % len(local)]})
            elif media:
                media.append(dict(media[-1]))
            else:
                raise RuntimeError(f"No visual for scene {i+1}")

    # Captions
    words_per_scene = [len(sc.get("say","").split()) for sc in scenes]
    total_words = max(sum(words_per_scene), 1)
    caption_items = []
    starts = [sum(lens[:i]) for i in range(n)]
    for i, sc in enumerate(scenes):
        st = starts[i]; en = starts[i] + lens[i]
        cap = sc.get("caption", sc.get("say",""))
        caption_items.append((st, en, cap))

    labels = [(starts[i], starts[i]+lens[i], sc.get("label","")) for i, sc in enumerate(scenes)]
    hook = script.get("hook","")
    cta = "Roz ek naya video  •  Follow karo"
    ass = bd / "subs.ass"
    _make_ass_shorts(caption_items, hook, cta, total, labels, ass, disclaimer)

    # Render
    music = _pick_music()
    out = cfg.OUTPUT_DIR / f"shorts_{job_id}.mp4"
    shorts_render(media, lens, cuts, voice_path, ass, total, out, music)
    _quality_check(job_id, out, ass, bd)
    # Create topic-based thumbnail options automatically; no MP4 download is needed.
    try:
        from core.thumbnail import generate_topic_variants
        thumb_dir = bd / "thumbnails"
        thumbs = generate_topic_variants(script.get("title", "New video"), script, thumb_dir,
                                         channel=cfg.CHANNEL_NAME)
        update_video(job_id, thumb_path=str(thumbs[0]))
    except Exception as exc:
        print(f"Thumbnail generation skipped: {exc}")
    update_video(job_id, video_path=str(out), status="done")
    return out


def build_longform(script: dict, job_id: int, build_dir: Path = None, language: str = "hindi", voice_gender: str = "male") -> Path:
    """Build a 16:9 long-form video from a section-based script."""
    from core.editor_longform import render as lf_render
    import subprocess
    bd = build_dir or (cfg.BUILD_DIR / f"job_{job_id}")
    bd.mkdir(parents=True, exist_ok=True)
    sections = script.get("sections", [])
    if not sections:
        raise ValueError("Long-form script must contain at least one section.")
    if any(not str(sec.get("narration", "")).strip() for sec in sections):
        raise ValueError("Every long-form section must contain non-empty narration.")
    cat = script.get("category","")
    disclaimer = cat in ("finance","markets","money")

    # Build full narration
    full_narration = " ".join(sec.get("narration","") for sec in sections)
    voice_path, timings = synthesise(full_narration, bd, language=language, gender=voice_gender)
    total = float(subprocess.check_output(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(voice_path)]
    ).strip())

    # Distribute time across sections by word count
    word_counts = [max(len(sec.get("narration","").split()),1) for sec in sections]
    total_words = sum(word_counts)
    used_ids = set()
    t_cursor = 0.0
    for i, sec in enumerate(sections):
        sec["_start"] = t_cursor
        sec["_dur"] = total * word_counts[i] / total_words
        t_cursor += sec["_dur"]

    # Build a multi-shot visual timeline from each section's visual beat list.
    local = _local_clips()
    shot_timeline = []
    for i, sec in enumerate(sections):
        narration = str(sec.get("narration", ""))
        beats = _visual_beats_for_section(sec)
        durations = _visual_beat_durations(narration, beats, sec["_dur"])
        shot_cursor = sec["_start"]
        for j, (beat, shot_dur) in enumerate(zip(beats, durations)):
            shot = dict(sec)
            shot["title"] = sec.get("title", "") if j == 0 else ""
            shot["_start"] = shot_cursor
            shot["_dur"] = shot_dur
            shot_cursor += shot_dur
            img_p = bd / f"limg{i}_{j}.jpg"
            prompt = ("Create a specific documentary shot illustrating ONLY this narration moment. "
                      "Keep subject, action, era and setting faithful to the description. "
                      "Photorealistic premium 16:9 B-roll, clear focal subject, realistic lighting, "
                      "no text, labels, watermarks or logos. Shot: " + beat["prompt"])
            if generate_image(prompt, img_p):
                shot["_media"] = {"type": "image", "path": str(img_p)}
            else:
                clip_p = bd / f"lraw{i}_{j}.mp4"
                kws = beat["keywords"] or sec.get("keywords", ["documentary"])
                if fetch_clip(kws, clip_p, used_ids):
                    shot["_media"] = {"type": "video", "path": str(clip_p)}
                elif local:
                    shot["_media"] = {"type": "video", "path": str(local[(i+j) % len(local)])}
                else:
                    shot["_media"] = {}
            shot_timeline.append(shot)

    if not any(item.get("_media", {}).get("path") for item in shot_timeline):
        raise RuntimeError("No usable long-form visuals were generated. Configure an image provider or add licensed clips.")

    ass = bd / "subs.ass"
    _make_ass_longform(timings, shot_timeline, total, ass, disclaimer)

    music = _pick_music()
    out = cfg.OUTPUT_DIR / f"longform_{job_id}.mp4"
    lf_render(shot_timeline, voice_path, ass, total, out, music)
    _quality_check(job_id, out, ass, bd)
    # Create topic-based thumbnail options automatically; no MP4 download is needed.
    try:
        from core.thumbnail import generate_topic_variants
        thumb_dir = bd / "thumbnails"
        thumbs = generate_topic_variants(script.get("title", "New video"), script, thumb_dir,
                                         channel=cfg.CHANNEL_NAME)
        update_video(job_id, thumb_path=str(thumbs[0]))
    except Exception as exc:
        print(f"Thumbnail generation skipped: {exc}")
    update_video(job_id, video_path=str(out), status="done")
    return out

import subprocess
