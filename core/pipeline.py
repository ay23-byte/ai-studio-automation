"""Main pipeline: takes a script dict, builds a video, returns output path."""
import random, re, subprocess, os
from pathlib import Path
from core import config as cfg
from core.voice import synthesise
from core.images import generate_image, fetch_clip, generate_ai_video
from core.db import save_video, update_video
from core.cinematic_shot_planner import plan_cinematic_shots, write_cinematic_shot_plan

from core import ffmpeg_utils
from core.jobs import Checkpoint, stable_hash

W_S, H_S = 1080, 1920   # Shorts
W_L, H_L = 1920, 1080   # Long-form
FADE = 0.3
TAIL = 0.6

def validate_documentary_visual_coverage(shot_timeline, narration_seconds,
                                           min_non_presenter_ratio=0.65,
                                           max_presenter_ratio=0.25):
    """Fail closed if presenter footage displaces the documentary's actual visuals."""
    total = max(float(narration_seconds), 0.0)
    if total <= 0:
        raise ValueError("Narration duration must be positive for visual coverage QA.")
    presenter_seconds = 0.0
    non_presenter_seconds = 0.0
    for shot in shot_timeline:
        media = shot.get("_media") or {}
        if not media.get("path"):
            continue
        duration = max(0.0, float(shot.get("_dur") or 0.0))
        if shot.get("presenter_mode"):
            presenter_seconds += duration
        else:
            non_presenter_seconds += duration
    presenter_ratio = presenter_seconds / total
    non_presenter_ratio = non_presenter_seconds / total
    if presenter_ratio > max_presenter_ratio:
        raise RuntimeError(
            f"Presenter footage occupies {presenter_ratio:.1%} of the documentary; "
            f"maximum allowed is {max_presenter_ratio:.0%}. Presenter must be an intro, not the documentary."
        )
    if non_presenter_ratio < min_non_presenter_ratio:
        raise RuntimeError(
            f"Only {non_presenter_ratio:.1%} of the documentary has non-presenter visuals; "
            f"at least {min_non_presenter_ratio:.0%} is required."
        )
    return {
        "narration_seconds": round(total, 3),
        "presenter_seconds": round(presenter_seconds, 3),
        "non_presenter_seconds": round(non_presenter_seconds, 3),
        "presenter_ratio": round(presenter_ratio, 4),
        "non_presenter_ratio": round(non_presenter_ratio, 4),
        "minimum_non_presenter_ratio": min_non_presenter_ratio,
        "maximum_presenter_ratio": max_presenter_ratio,
    }


def _media_seconds(path) -> float:
    """Duration of a rendered asset; a zero-length asset is a failed stage."""
    seconds = ffmpeg_utils.probe_duration(path)
    if seconds <= 0:
        raise RuntimeError(f"Could not read a valid duration from {Path(path).name}.")
    return seconds


def _caption_fonts(header: str, *samples) -> str:
    """Use a Devanagari-capable family when any visible caption is Hindi.

    Root cause: every style named ``Noto Sans`` (Latin only), so Hindi captions
    depended on whatever fallback font the machine happened to have.
    """
    from core.fonts import ass_font_name
    family = ass_font_name(" ".join(str(sample or "") for sample in samples))
    return header if family == "Noto Sans" else header.replace(",Noto Sans,", f",{family},")


def _voice_stage(checkpoint, text, bd, language, gender):
    """Narration with checkpoint reuse: identical text/voice is never re-synthesised."""
    import json
    timings_path = Path(bd) / "voice_timings.json"
    voice_path = Path(bd) / "voice.wav"
    signature = stable_hash(text, language, gender, os.getenv("TTS_ENGINE", "edge"),
                            os.getenv("TTS_VOICE", ""), os.getenv("TTS_PRONUNCIATION_MAP", ""))

    def run():
        path, timings = synthesise(text, bd, language=language, gender=gender)
        timings_path.write_text(json.dumps([[float(a), float(b), str(c)] for a, b, c in timings],
                                           ensure_ascii=False), encoding="utf-8")
        return str(path)

    path = checkpoint.run("voice", run, input_hash=signature,
                          files=[voice_path.name, timings_path.name])
    try:
        timings = [(float(a), float(b), str(c)) for a, b, c in
                   json.loads(timings_path.read_text(encoding="utf-8"))]
    except (OSError, ValueError) as exc:
        raise RuntimeError("Narration timings are missing or unreadable.") from exc
    return Path(path or voice_path), timings


def _fallback_card(path, text, *, size, kicker, index, total, category, keywords=None):
    """Last-resort original explainer graphic (see core.explainer_cards)."""
    from core import explainer_cards
    if not explainer_cards.enabled():
        return False
    try:
        explainer_cards.render_card(path, text, size=size, kicker=kicker, index=index,
                                    total=total, category=category, keywords=keywords,
                                    channel=cfg.CHANNEL_NAME)
        return Path(path).is_file() and Path(path).stat().st_size > 1000
    except Exception as exc:
        print(f"WARNING: fallback explainer card could not be drawn: {exc}")
        return False


def _apply_fallback_gate(report_path, fallback_report):
    """Fold fallback-card usage into the quality report and the upload gate."""
    import json
    if not fallback_report.get("fallback_card_count"):
        return None
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    count, total = fallback_report["fallback_card_count"], fallback_report["visual_count"]
    message = (f"{count} of {total} visuals are locally generated fallback cards because no "
               "scene-matched image/footage provider responded.")
    report.setdefault("warnings", []).append(message)
    report["visual_fallback"] = fallback_report
    if report.get("status") == "PASS":
        report["status"] = "REVIEW"
    report["score"] = max(0, int(report.get("score", 100)) - (20 if fallback_report["status"] == "FAIL" else 8))
    if fallback_report["status"] == "FAIL":
        report["upload_blocked"] = True
        report["upload_block_reason"] = (
            message + f" This exceeds the allowed share ({fallback_report['max_share_allowed']:.0%}); "
            "automatic upload is blocked until visuals are replaced or a human overrides "
            "with ALLOW_FALLBACK_VISUAL_UPLOAD=1.")
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"QUALITY WARNING: {message}")
    # The line printed by the technical check precedes this deduction; print the
    # final figures so the log agrees with quality_report.json and the database.
    print(f"Final video quality after the fallback-visual check: {report['status']} ({report['score']}/100)")
    return report


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
            "clean": "Style: Cap,Noto Sans,82,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,70,70,620,1",
            "karaoke": "Style: Cap,Noto Sans,104,&H0000FFFF,&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,1,10,4,2,70,70,640,1",
            "bold": "Style: Cap,Noto Sans,96,&H0000FFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,9,3,2,70,70,640,1",
        }
    else:
        styles = {
            "clean": "Style: Cap,Noto Sans,52,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,0,0,0,0,100,100,0,0,1,3,1,2,120,120,60,1",
            "karaoke": "Style: Cap,Noto Sans,66,&H0000FFFF,&H000000FF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,1,7,3,2,120,120,60,1",
            "bold": "Style: Cap,Noto Sans,58,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,0,0,0,0,100,100,0,0,1,5,2,2,120,120,60,1",
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
Style: Hook,Noto Sans,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,8,3,8,90,90,300,1
Style: Label,Noto Sans,52,&H0000D7FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,3,0,1,6,2,8,80,80,175,1
Style: End,Noto Sans,68,&H0000A5FF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,80,80,1000,1
Style: Mark,Noto Sans,38,&H66FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,2,0,1,2,0,7,50,50,90,1
Style: Disc,Noto Sans,32,&H99FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,3,0,2,60,60,310,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    header = _caption_fonts(header, hook, cta, *[text for _, _, text in items],
                            *[lbl for _, _, lbl in (labels or [])])
    lines = [f"Dialogue: 0,{_ts(0)},{_ts(T)},Mark,,0,0,0,,{ch_name}"]
    if disclaimer:
        lines.append(f"Dialogue: 0,{_ts(0)},{_ts(T)},Disc,,0,0,0,,For educational purposes only  |  Not investment advice")
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


def _visible_spans(total, quiet_windows):
    """[0, total] minus the quiet windows, as (start, end) spans of at least 0.2 s."""
    spans, cursor = [], 0.0
    for start, end in sorted((max(0.0, float(a)), min(float(total), float(b))) for a, b in quiet_windows or []):
        if end <= start:
            continue
        if start - cursor >= 0.2:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if float(total) - cursor >= 0.2:
        spans.append((cursor, float(total)))
    return spans


def _make_ass_longform(timings, sections, total, path, disclaimer, quiet_windows=None):
    """Write documentary captions.

    ``quiet_windows`` are (start, end) spans occupied by full-screen title slates.
    The slate already carries the chapter title, so the watermark, disclaimer and
    section label are held back there instead of being drawn over its text.
    """
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
Style: Mark,Noto Sans,24,&H66FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,40,40,22,1
Style: Disc,Noto Sans,24,&H99FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,9,80,80,22,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    header = _caption_fonts(header, *[text for _, _, text in timings],
                            *[sec.get("subtitle_title", sec.get("title", "")) for sec in sections])
    quiet = [(float(a), float(b)) for a, b in (quiet_windows or []) if float(b) > float(a)]
    lines = []
    for span_start, span_end in _visible_spans(T, quiet):
        lines.append(f"Dialogue: 0,{_ts(span_start)},{_ts(span_end)},Mark,,0,0,0,,{cfg.CHANNEL_NAME}")
        if disclaimer:
            lines.append(f"Dialogue: 0,{_ts(span_start)},{_ts(span_end)},Disc,,0,0,0,,For educational purposes only  |  Not investment advice")
    for sec in sections:
        st = sec.get("_start", 0)
        label = sec.get("subtitle_title", sec.get("title", ""))[:40]
        if any(a - 0.05 <= st + 0.2 < b for a, b in quiet):
            continue  # a title slate is on screen and already shows this heading
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


def _visual_beats_for_section(section, category="general"):
    from core.documentary_storyboard import plan_section_visuals
    planned = plan_section_visuals(section, category)
    beats = []
    for item in planned[:10]:
        # Preserve the original concise prompt for legacy string storyboards;
        # structured visual beats receive the full production prompt.
        is_structured = bool(item.get("_structured_input"))
        prompt = str((item.get("production_prompt") if is_structured else item.get("visual_prompt")) or item.get("visual_prompt") or "").strip()
        anchor = str(item.get("narration_anchor") or item.get("anchor") or "").strip()
        keywords = item.get("search_queries") or item.get("keywords") or section.get("keywords") or []
        beats.append({
            "prompt": prompt, "anchor": anchor,
            "keywords": [str(k).strip() for k in keywords if str(k).strip()][:8],
            "visual_type": item.get("visual_type", "stock"),
            "subject": item.get("subject", ""), "location": item.get("location", ""),
            "time_period": item.get("time_period", ""),
            "shot_size": item.get("shot_size", "medium or wide establishing shot"),
            "camera_movement": item.get("camera_movement", ""),
            "visual_guardrail": item.get("visual_guardrail", ""),
            "source_refs": item.get("source_refs", []),
            "chart_spec": item.get("chart_spec"),
            "timeline_events": item.get("timeline_events", []),
            "on_screen_text": item.get("on_screen_text", ""),
            "sound_design": item.get("sound_design", ""),
        })
    return beats


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


def _slate_budget(section_seconds, consumed_seconds, visual_durations, minimum_visual_seconds=1.0):
    """Length of a chapter slate and the visual durations that fit after it.

    ``consumed_seconds`` is the part of the chapter already filled (a presenter
    intro).  Slate plus visuals always equals what is left of the chapter, so the
    picture track stays the same length as that chapter's narration.  Returns
    ``(0.0, visual_durations)`` when there is no room for a slate.
    """
    section = max(float(section_seconds), 0.0)
    available = max(section - max(float(consumed_seconds), 0.0), 0.0)
    durations = [max(float(value), 0.0) for value in visual_durations]
    title = min(2.4, section * 0.12)
    planned = sum(durations)
    if title < 1.5 or planned <= 0 or available - title < minimum_visual_seconds:
        return 0.0, durations
    scale = (available - title) / planned
    return title, [value * scale for value in durations]


def _timeline_drift(shot_timeline, sections):
    """Largest gap, in seconds, between a chapter's shots and its narration length."""
    worst = 0.0
    for index, sec in enumerate(sections):
        shots = [shot for shot in shot_timeline if shot.get("_chapter_index", None) == index]
        if not shots:
            continue
        placed = sum(max(float(shot.get("_dur") or 0.0), 0.0) for shot in shots)
        worst = max(worst, abs(placed - float(sec.get("_dur") or 0.0)))
    return worst


def _quality_check(job_id, video_path, subtitle_path, build_dir, documentary=False, body_seconds=None):
    """Run required post-render QA; fail closed when inspection fails or cannot run.

    ``body_seconds`` is the programme length before an appended credit slate, so
    the slate is not reported as black, silent footage.
    """
    try:
        from core.quality import inspect_video, write_report
        options = {"profile": "longform"} if documentary else {}
        if body_seconds:
            options["body_seconds"] = float(body_seconds)
        report = inspect_video(video_path, subtitle_path=subtitle_path, **options)
        if documentary:
            from core.audio_quality import inspect_audio
            audio_report = inspect_audio(video_path, **({"body_seconds": float(body_seconds)} if body_seconds else {}))
            write_report(audio_report, Path(build_dir) / "audio_quality_report.json")
            report["audio_mastering"] = audio_report
            if audio_report.get("status") == "FAIL":
                report["issues"].extend(
                    f"Audio mastering: {message}" for message in audio_report.get("issues", [])
                )
                report["status"] = "FAIL"
                report["score"] = max(0, report["score"] - 20 * max(1, len(audio_report.get("issues", []))))
            elif audio_report.get("status") in {"REVIEW", "ERROR"}:
                report["warnings"].extend(
                    f"Audio mastering: {message}"
                    for message in (audio_report.get("warnings") or audio_report.get("issues") or [])
                )
                if report["status"] == "PASS":
                    report["status"] = "REVIEW"
                report["score"] = max(0, report["score"] - 8 * max(1, len(audio_report.get("warnings", []))))
        report_path = write_report(report, Path(build_dir) / "quality_report.json")
        update_video(job_id, quality_report_path=str(report_path),
                     quality_score=report["score"], quality_status=report["status"])
        print(f"Video quality check: {report['status']} ({report['score']}/100)")
        for warning in report.get("warnings", []):
            print(f"QUALITY WARNING: {warning}")
        for issue in report.get("issues", []):
            print(f"QUALITY ERROR: {issue}")
        if report.get("status") == "FAIL":
            raise RuntimeError(
                "Video quality inspection failed; refusing to mark this render complete. "
                + "; ".join(str(item) for item in report.get("issues", []))
            )
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "Video quality inspection failed" in str(exc):
            raise
        print(f"Video quality inspection could not complete: {exc}")
        raise RuntimeError(
            "Required video quality inspection could not complete; refusing an unverified render."
        ) from exc


def _short_scene_visual(index, scene, scenes, script, bd, used_ids, state):
    """Find the best available visual for one Shorts scene (video > image > stock > card)."""
    img_p = bd / f"img{index}.jpg"
    clip_p = bd / f"raw{index}.mp4"
    card_p = bd / f"card{index}.jpg"
    scene_prompt = (
        "Create a visual that directly illustrates this exact spoken narration. "
        "Do not use unrelated generic filler. Spoken line: "
        + str(scene.get("say", "")) + ". Visual direction: "
        + str(scene.get("image") or scene.get("say", ""))
        + ". 9:16 cinematic documentary image, accurate subject and action, no text or logos."
    )
    ai_video_mode = os.getenv("AI_VIDEO_MODE", "fallback").strip().lower()
    has_local_video_model = bool(os.getenv("OPENVINO_LTX_MODEL_DIR", "").strip())
    ai_video_attempted = False
    if ai_video_mode == "prefer" and has_local_video_model and not state["ai_video_failed"]:
        ai_video_attempted = True
        if generate_ai_video(scene_prompt, clip_p, aspect_ratio="9:16"):
            return {"type": "video", "path": clip_p, "provider": "ai_video"}
        state["ai_video_failed"] = True
    # After repeated consecutive failures the image provider is down or rate
    # limited: stop paying its retry/back-off cost for every remaining scene.
    if state["image_failures"] < state["image_failure_limit"]:
        if generate_image(scene_prompt, img_p, category=script.get("category", "general")):
            state["image_failures"] = 0
            return {"type": "image", "path": img_p, "provider": "image"}
        state["image_failures"] += 1
        if state["image_failures"] == state["image_failure_limit"]:
            print("  image provider failed repeatedly; skipping it for the remaining scenes.")
    if not ai_video_attempted and has_local_video_model and not state["ai_video_failed"]:
        if generate_ai_video(scene_prompt, clip_p, aspect_ratio="9:16"):
            return {"type": "video", "path": clip_p, "provider": "ai_video"}
        state["ai_video_failed"] = True
    keywords = scene.get("keywords") or [str(scene.get("say", ""))[:100]]
    if fetch_clip(keywords, clip_p, used_ids, scene_prompt=scene_prompt):
        return {"type": "video", "path": clip_p, "provider": "stock"}
    card_text = " ".join(str(scene.get(key) or "") for key in ("label", "caption", "say")).strip()
    # The scene label is already burned in by the caption track; no kicker here.
    if _fallback_card(card_p, card_text, size=(W_S, H_S), kicker="",
                      index=index, total=len(scenes), category=script.get("category", "general"),
                      keywords=None):
        print(f"  scene {index + 1}: no provider responded; using a disclosed explainer card.")
        return {"type": "image", "path": card_p, "provider": "fallback_card"}
    raise RuntimeError(
        f"No scene-matched visual was available for Shorts scene {index + 1}. "
        "Image generation and relevant stock search both failed; refusing to insert unrelated footage. "
        "Retry later or configure a working image provider/Pixabay key."
    )


def build_shorts(script: dict, job_id: int, build_dir: Path = None, language: str = "english", voice_gender: str = "male") -> Path:
    """Build a 9:16 Shorts video from a scene-based script.

    Stages are checkpointed in ``build/job_<id>/checkpoint.json``; re-running the
    same job after a failure reuses the narration and every visual already made.
    """
    from core.editor_shorts import render as shorts_render
    import json
    bd = Path(build_dir) if build_dir else (cfg.BUILD_DIR / f"job_{job_id}")
    bd.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes", [])
    if not scenes:
        raise ValueError("Shorts script must contain at least one scene.")
    if any(not str(sc.get("say", "")).strip() for sc in scenes):
        raise ValueError("Every Shorts scene must contain non-empty narration in its say field.")
    cat = script.get("category", "")
    disclaimer = cat in ("finance", "markets", "money")
    checkpoint = Checkpoint(job_id, bd)
    (bd / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    update_video(job_id, status="building", error=None)

    try:
        # Voice
        scene_texts = [str(sc.get("say", "")) for sc in scenes]
        full_text = " ".join(scene_texts)
        voice_path, timings = _voice_stage(checkpoint, full_text, bd, language, voice_gender)
        total = _media_seconds(voice_path)

        # Scene timing follows the measured narration, not an equal split, so
        # each visual and caption changes when its sentence is actually spoken.
        from core.timing import align_units
        n = len(scenes)
        used_ids = set()
        T = total + TAIL
        spans = align_units(scene_texts, timings, total)
        starts = [span[0] for span in spans]
        lens = [span[1] - span[0] for span in spans]
        lens[-1] += TAIL
        cuts = starts[1:]
        (bd / "scene_timing.json").write_text(json.dumps([
            {"scene": i + 1, "start": round(starts[i], 3), "duration": round(lens[i], 3),
             "say": scene_texts[i]} for i in range(n)
        ], ensure_ascii=False, indent=2), encoding="utf-8")

        # Visuals (each scene is its own resumable stage)
        media = []
        state = {"ai_video_failed": False, "image_failures": 0,
                 "image_failure_limit": max(1, int(os.getenv("IMAGE_PROVIDER_FAILURE_LIMIT", "2")))}
        for i, sc in enumerate(scenes):
            signature = stable_hash(sc.get("say"), sc.get("image"), sc.get("keywords"), cat)
            record = checkpoint.get(f"visual_{i}") or {}
            stored = record.get("result") or {}
            was_card = stored.get("provider") == "fallback_card"
            retry_cards = os.getenv("RETRY_FALLBACK_CARDS_ON_RESUME", "1").strip().lower() in {"1", "true", "yes"}

            def acquire(i=i, sc=sc):
                item = _short_scene_visual(i, sc, scenes, script, bd, used_ids, state)
                return {"type": item["type"], "path": str(item["path"]), "provider": item["provider"]}

            if was_card and retry_cards and record.get("status") == "done":
                # A resumed job gets another chance to replace a fallback card.
                checkpoint.mark(f"visual_{i}", "stale", input_hash="retry-card")
            item = checkpoint.run(
                f"visual_{i}", acquire, input_hash=signature,
                files=[Path(stored["path"]).name] if stored.get("path") else (),
            )
            media.append({"type": item["type"], "path": Path(item["path"]), "provider": item.get("provider")})

        from core import explainer_cards
        fallback_report = explainer_cards.write_fallback_report(
            bd, [item["path"] for item in media], explainer_cards.max_share(0.5))

        # Apply deterministic camera/lens shot planning to every visual; local metadata only.
        short_shots = [{
            "visual_prompt": str(scene.get("image") or scene.get("say") or ""),
            "narration": str(scene.get("say") or ""),
            "camera_movement": str(scene.get("camera_movement") or "slow controlled push-in"),
            "shot_size": str(scene.get("shot_size") or "medium"),
            "visual_guardrail": "Match the spoken line; no unrelated filler or fabricated evidence.",
            "cinema_settings": scene.get("cinema_settings") or {},
        } for scene in scenes]
        planned_short_shots = plan_cinematic_shots(short_shots, defaults={"aspect_ratio": "9:16"})
        for media_item, planned_shot in zip(media, planned_short_shots):
            media_item["cinema_settings"] = planned_shot["cinema_settings"]
            media_item["cinematic_prompt"] = planned_shot["cinematic_prompt"]
        write_cinematic_shot_plan(short_shots, bd / "cinematic_shot_plan.json", defaults={"aspect_ratio": "9:16"})

        # Asset provenance for every visual (provider, licence, attribution).
        try:
            from core.asset_manifest import write_shorts_asset_manifest
            write_shorts_asset_manifest(bd, scenes, media)
        except Exception as exc:
            print(f"WARNING: asset manifest could not be written: {exc}")

        # Captions
        caption_items = []
        for i, sc in enumerate(scenes):
            st = starts[i]; en = starts[i] + lens[i] - (TAIL if i == n - 1 else 0.0)
            cap = sc.get("caption", sc.get("say",""))
            caption_items.append((st, en, cap))

        labels = [(starts[i], starts[i]+lens[i], sc.get("label","")) for i, sc in enumerate(scenes)]
        hook = script.get("hook","")
        cta = str(script.get("cta") or os.getenv("SHORTS_CTA", "").strip()
                  or "Follow for more engineering and science videos")
        ass = bd / "subs.ass"
        _make_ass_shorts(caption_items, hook, cta, total, labels, ass, disclaimer)
        try:
            from core.subtitles_export import write_sidecars, LANGUAGE_CODES
            write_sidecars(timings, bd, "captions", LANGUAGE_CODES.get(str(language).lower(), ""))
        except Exception as exc:
            print(f"WARNING: SRT/VTT caption sidecars could not be written: {exc}")

        # Render
        music = _pick_music()
        cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = cfg.OUTPUT_DIR / f"shorts_{job_id}.mp4"
        checkpoint.mark("render", "running")
        shorts_render(media, lens, cuts, voice_path, ass, total, out, music, work_dir=bd / "render_work")
        checkpoint.mark("render", "done", files=[str(out)])
        _quality_check(job_id, out, ass, bd)
        gated = _apply_fallback_gate(bd / "quality_report.json", fallback_report)
        if gated:
            update_video(job_id, quality_score=gated["score"], quality_status=gated["status"])
        checkpoint.mark("quality", "done")
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
    except BaseException as exc:
        try:
            update_video(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
        except Exception:
            pass
        raise


def build_longform(script: dict, job_id: int, build_dir: Path = None, language: str = "hindi", voice_gender: str = "male",
                   subtitle_language: str | None = None) -> Path:
    """Build a 16:9 long-form video from a section-based script.

    ``subtitle_language`` (or ``SUBTITLE_LANGUAGE``): english (default), hindi,
    hinglish, or ``source`` for captions exactly as spoken. Stages are
    checkpointed; re-running the same job reuses narration, subtitle
    translation, acquired visuals and rendered segments.
    """
    from core.editor_longform import render as lf_render
    import subprocess
    bd = build_dir or (cfg.BUILD_DIR / f"job_{job_id}")
    bd.mkdir(parents=True, exist_ok=True)
    import json
    # Persist the exact generated storyboard so later review/mapping passes can
    # rerender the same timeline instead of regenerating narration with Gemini.
    (bd / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    release_metadata = {
        "title": str(script.get("title") or script.get("video_title") or "").strip(),
        "description": str(script.get("description") or "").strip(),
        "tags": script.get("tags") or [],
        "sources": script.get("sources") or [],
        "claims": script.get("claims") or [],
    }
    (bd / "release_metadata.json").write_text(
        json.dumps(release_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sections = script.get("sections", [])
    if not sections:
        raise ValueError("Long-form script must contain at least one section.")
    if any(not str(sec.get("narration", "")).strip() for sec in sections):
        raise ValueError("Every long-form section must contain non-empty narration.")
    from core.research_verification import write_research_verification_report
    research_report = write_research_verification_report(
        script, script.get("research_sources", []), bd
    )
    print(
        f"Research verification dossier: {research_report['source_count']} sources, "
        f"{research_report['claim_count']} claims; human fact-check required."
    )
    # Validate new structured storyboards before generating any media.
    if any(any(isinstance(item, dict) for item in (sec.get("visuals") or [])) for sec in sections):
        from core.script_engine import validate_longform_storyboard
        validate_longform_storyboard(script)
    cat = script.get("category","")
    disclaimer = cat in ("finance","markets","money")

    # Build full narration
    full_narration = " ".join(sec.get("narration","") for sec in sections)
    checkpoint = Checkpoint(job_id, bd)
    update_video(job_id, status="building", error=None)
    voice_path, timings = _voice_stage(checkpoint, full_narration, bd, language, voice_gender)
    total = _media_seconds(voice_path)

    # Chapter boundaries follow the measured narration (phrase timings from the
    # voice stage) instead of a word-count estimate, so chapter slates, lower
    # thirds and visuals change exactly when each chapter is spoken.
    from core.timing import align_units
    used_ids = set()
    section_spans = align_units([str(sec.get("narration", "")) for sec in sections], timings, total,
                                min_duration=3.0)
    for sec, (span_start, span_end) in zip(sections, section_spans):
        sec["_start"] = span_start
        sec["_dur"] = span_end - span_start
    fallback_state = {"cards": [], "reused": 0}

    # Documentary subtitles are translated independently from the narration voice.
    # The spoken audio and its timing remain untouched; only visible subtitle text
    # changes. This runs BEFORE visuals so a translation outage fails fast instead
    # of after every asset has been produced, and its result is checkpointed.
    from core.subtitle_translation import translate_longform_subtitles, normalize_subtitle_language
    subtitle_target = normalize_subtitle_language(
        subtitle_language or os.getenv("SUBTITLE_LANGUAGE", "english"))
    translation_path = bd / "subtitle_translation.json"

    def _translate():
        try:
            lines, titles = translate_longform_subtitles(
                timings, sections, target_language=subtitle_target, source_language=language)
            mode = subtitle_target
        except Exception as exc:
            if os.getenv("SUBTITLE_TRANSLATION_FALLBACK", "fail").strip().lower() != "source":
                raise
            print(f"WARNING: subtitle translation failed ({exc}); showing captions as spoken "
                  "because SUBTITLE_TRANSLATION_FALLBACK=source.")
            lines, titles = list(timings), [str(sec.get("title") or "") for sec in sections]
            mode = "source_fallback"
        translation_path.write_text(json.dumps(
            {"target": subtitle_target, "mode": mode,
             "captions": [[float(a), float(b), str(c)] for a, b, c in lines], "titles": titles},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return {"target": subtitle_target, "mode": mode}

    checkpoint.run("subtitles", _translate, files=[translation_path.name],
                   input_hash=stable_hash(subtitle_target, language, [t[2] for t in timings],
                                          [sec.get("title") for sec in sections]))
    _translated = json.loads(translation_path.read_text(encoding="utf-8"))
    subtitle_timings = [(float(a), float(b), str(c)) for a, b, c in _translated["captions"]]
    subtitle_titles = list(_translated["titles"])

    # Build a multi-shot visual timeline from each section's visual beat list.
    # Optional SadTalker chapter intros use the exact narration audio segment.
    shot_timeline = []
    missing_visuals = []
    presenter_mode = os.getenv("PRESENTER_MODE", "still_cpu").strip().lower()
    presenter_enabled = os.getenv("PRESENTER_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    presenter_image = os.getenv("PRESENTER_IMAGE", "assets/presenter/presenter_master.png")
    if presenter_enabled and presenter_mode == "talking_avatar_auto":
        from core.avatar_clip_generation import generate_chapter_avatar_clips
        generate_chapter_avatar_clips(voice_path, sections, voice_gender, bd)
        presenter_mode = "talking_avatar"
    for i, sec in enumerate(sections):
        narration = str(sec.get("narration", ""))
        beats = _visual_beats_for_section(sec, script.get('category', 'general'))
        durations = _visual_beat_durations(narration, beats, sec["_dur"])
        shot_cursor = sec["_start"]
        if presenter_enabled and presenter_mode == "still_cpu":
            from core.presenter_scene import render_presenter_scene
            presenter_duration = min(5.0, max(2.5, sec["_dur"] * 0.16))
            presenter_clip = render_presenter_scene(
                i + 1, presenter_duration, bd, image_path=presenter_image
            )
            if presenter_clip:
                actual_duration = float(subprocess.check_output(
                    [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(presenter_clip)]
                , timeout=PROBE_TIMEOUT).strip())
                from core.presenter_quality import write_presenter_metadata
                write_presenter_metadata(presenter_clip, {
                    "provider": "AI Studio CPU still presenter", "presenter_mode": "still_cpu",
                    "portrait_asset": Path(presenter_image).name,
                    "licence_status": "Confirm ownership/permission for the presenter portrait before publication.",
                    "lip_sync_verified": False,
                    "notice": "This mode animates a still portrait; it is not lip-sync.",
                })
                presenter_shot = dict(sec)
                presenter_shot["_chapter_index"] = i
                presenter_shot["title"] = sec.get("title", "")
                presenter_shot["presenter_mode"] = "still_cpu"
                presenter_shot["presenter_sync_status"] = "not_lipsynced"
                presenter_shot["presenter_lip_sync_verified"] = False
                presenter_shot["_start"] = shot_cursor
                presenter_shot["_dur"] = actual_duration
                presenter_shot["_media"] = {"type": "video", "path": str(presenter_clip)}
                shot_timeline.append(presenter_shot)
                shot_cursor += actual_duration
                remaining = max(sec["_dur"] - actual_duration, 0.0)
                scale = remaining / max(sum(durations), 0.001)
                durations = [value * scale for value in durations]
        elif presenter_enabled and presenter_mode == "talking_avatar":
            # Use one pre-rendered browser avatar clip per chapter. The editor
            # mutes source-video audio and keeps the continuous narration track.
            from core.talking_avatar import find_section_clip, probe_duration
            avatar_clip = find_section_clip(i + 1)
            if avatar_clip is None:
                raise FileNotFoundError(
                    f"Talking Avatar clip missing for chapter {i + 1}. "
                    "Create it in avatar_studio from this chapter's narration audio, "
                    f"then save it as assets/presenter_clips/presenter_section_{i + 1:02d}.mp4 "
                    "(or .webm). Set PRESENTER_MODE=talking_avatar."
                )
            actual_duration = probe_duration(avatar_clip)
            if actual_duration <= 0 or actual_duration > sec["_dur"] + 0.75:
                raise ValueError(
                    f"Talking Avatar clip for chapter {i + 1} is {actual_duration:.2f}s, "
                    f"but the chapter narration is {sec['_dur']:.2f}s. Recreate the clip "
                    "from that chapter's exact narration audio so lip-sync stays aligned."
                )
            from core.presenter_quality import inspect_presenter_clip, write_presenter_metadata
            validation = inspect_presenter_clip(
                avatar_clip, expected_duration=actual_duration,
                tolerance_seconds=0.75, require_audio=True,
            )
            if validation["status"] == "FAIL":
                raise ValueError(
                    f"Talking Avatar clip for chapter {i + 1} failed A/V validation: "
                    + "; ".join(validation["issues"])
                )
            write_presenter_metadata(avatar_clip, {
                "provider": "Talking Avatar SDK", "presenter_mode": "talking_avatar",
                "validation": validation, "lip_sync_verified": False,
                "licence_status": "Review SDK and built-in avatar licence terms before redistribution or commercial use.",
                "notice": "The clip includes its driving audio and passes stream/duration checks; phoneme-level lip-sync still requires human review.",
            })
            presenter_shot = dict(sec)
            presenter_shot["_chapter_index"] = i
            presenter_shot["title"] = sec.get("title", "")
            presenter_shot["presenter_mode"] = "talking_avatar"
            presenter_shot["presenter_sync_status"] = (
                "duration_aligned" if validation["status"] == "PASS" else "duration_aligned_review"
            )
            presenter_shot["presenter_lip_sync_verified"] = False
            presenter_shot["_start"] = shot_cursor
            presenter_shot["_dur"] = actual_duration
            presenter_shot["_media"] = {"type": "video", "path": str(avatar_clip)}
            shot_timeline.append(presenter_shot)
            shot_cursor += actual_duration
            remaining = max(sec["_dur"] - actual_duration, 0.0)
            scale = remaining / max(sum(durations), 0.001)
            durations = [value * scale for value in durations]
        elif presenter_enabled and presenter_mode == "sadtalker":
            presenter_duration = min(6.0, max(2.0, sec["_dur"] * 0.22))
            presenter_clip = None
            sadtalker_used = False
            try:
                from core.presenter import generate_section_presenter
                presenter_clip = generate_section_presenter(
                    voice_path, sec["_start"], presenter_duration, i + 1, bd
                )
                sadtalker_used = presenter_clip is not None
            except Exception as exc:
                # Presenter animation is an enhancement, not a reason to lose
                # an otherwise complete documentary render.
                print(f"WARNING: SadTalker failed for chapter {i + 1}: {exc}")
                print("Falling back to the CPU still-image presenter.")
                try:
                    from core.presenter_scene import render_presenter_scene
                    presenter_clip = render_presenter_scene(
                        i + 1, presenter_duration, bd, image_path=presenter_image
                    )
                except Exception as fallback_exc:
                    print(f"WARNING: CPU presenter fallback also failed: {fallback_exc}")
                    presenter_clip = None
            if presenter_clip:
                actual_duration = float(subprocess.check_output(
                    [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(presenter_clip)]
                , timeout=PROBE_TIMEOUT).strip())
                from core.presenter_quality import write_presenter_metadata
                if not sadtalker_used:
                    write_presenter_metadata(presenter_clip, {
                        "provider": "AI Studio CPU still presenter fallback",
                        "presenter_mode": "still_cpu_fallback",
                        "portrait_asset": Path(presenter_image).name,
                        "licence_status": "Confirm ownership/permission for the presenter portrait before publication.",
                        "lip_sync_verified": False,
                        "notice": "SadTalker was unavailable; this fallback animates a still portrait and is not lip-sync.",
                    })
                presenter_metadata = {}
                sidecar = presenter_clip.with_suffix(presenter_clip.suffix + ".source.json")
                if sidecar.is_file():
                    try:
                        import json
                        presenter_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        presenter_metadata = {}
                sync_status = "not_lipsynced"
                if sadtalker_used:
                    validation = presenter_metadata.get("validation") or {}
                    sync_status = "duration_aligned" if validation.get("status") == "PASS" else "duration_aligned_review"
                presenter_shot = dict(sec)
                presenter_shot["_chapter_index"] = i
                presenter_shot["title"] = sec.get("title", "")
                presenter_shot["presenter_mode"] = "sadtalker" if sadtalker_used else "still_cpu_fallback"
                presenter_shot["presenter_sync_status"] = sync_status
                presenter_shot["presenter_lip_sync_verified"] = False
                presenter_shot["_start"] = shot_cursor
                presenter_shot["_dur"] = actual_duration
                presenter_shot["_media"] = {"type": "video", "path": str(presenter_clip)}
                shot_timeline.append(presenter_shot)
                shot_cursor += actual_duration
                remaining = max(sec["_dur"] - actual_duration, 0.0)
                scale = remaining / max(sum(durations), 0.001)
                durations = [value * scale for value in durations]
        elif presenter_enabled and presenter_mode not in {"still_cpu", "off", "none"}:
            raise ValueError(f"Unsupported PRESENTER_MODE: {presenter_mode}. Use talking_avatar_auto, talking_avatar, still_cpu, sadtalker, or off.")
        # A short animated chapter slate establishes clear documentary structure.
        # Root cause of drifting long-form visuals: the slate budget used the whole
        # chapter length and ignored the presenter intro already placed, so every
        # chapter overran by the intro's length and each later chapter's pictures
        # arrived that much further behind the narration.
        title_duration, slate_durations = _slate_budget(sec["_dur"], shot_cursor - sec["_start"], durations)
        if title_duration >= 1.5:
            try:
                from core.graphic_scenes import render_chapter_title
                title_clip = render_chapter_title(sec.get("title", f"Chapter {i+1}"),
                                                  i + 1, title_duration, bd / "chapter_slates")
            except Exception as exc:
                print(f"WARNING: chapter title slate skipped: {exc}")
                title_clip = None
            if title_clip:
                title_shot = dict(sec)
                title_shot.update({"_chapter_index": i, "visual_type": "chapter_title",
                                   "_start": shot_cursor, "_dur": title_duration,
                                   "_media": {"type": "video", "path": str(title_clip)}})
                shot_timeline.append(title_shot)
                shot_cursor += title_duration
                durations = slate_durations
        for j, (beat, shot_dur) in enumerate(zip(beats, durations)):
            shot = dict(sec)
            shot["title"] = sec.get("title", "") if j == 0 else ""
            shot["_start"] = shot_cursor
            shot["_chapter_index"] = i
            shot["_dur"] = shot_dur
            shot_cursor += shot_dur
            img_p = bd / f"limg{i}_{j}.jpg"
            prompt = ("Create a documentary shot illustrating ONLY this spoken narration moment. "
                      "Depict the same subject, action, process and setting; never substitute generic filler. "
                      "Photorealistic premium 16:9 B-roll, clear focal subject, realistic lighting, "
                      "no text, labels, watermarks or logos. Narration anchor: " + beat.get("anchor", "") +
                      ". Visual direction: " + beat["prompt"])
            shot.update({
                "narration": beat.get("anchor") or narration,
                "visual_prompt": prompt,
                "visual_type": beat.get("visual_type", "stock"),
                "subject": beat.get("subject", ""),
                "location": beat.get("location", ""),
                "time_period": beat.get("time_period", ""),
                "shot_size": beat.get("shot_size", ""),
                "camera_movement": beat.get("camera_movement", ""),
                "visual_guardrail": beat.get("visual_guardrail", ""),
                "source_refs": beat.get("source_refs", []),
                "chart_spec": beat.get("chart_spec"),
                "timeline_events": beat.get("timeline_events", []),
                "on_screen_text": beat.get("on_screen_text", ""),
                "sound_design": beat.get("sound_design", ""),
            })
            # Render structured data/evidence beats locally before requesting B-roll.
            # The renderer consumes only supplied values; unsupported specs fall through.
            graphic_path = None
            try:
                from core.graphic_scenes import render_graphic_scene
                graphic_path = render_graphic_scene(shot, shot_dur, bd, f"graphic_{i}_{j}")
            except Exception as exc:
                print(f"WARNING: graphic scene {i+1}.{j+1} failed; trying B-roll: {exc}")
            if graphic_path:
                shot["_media"] = {"type": "video", "path": str(graphic_path)}
                shot_timeline.append(shot)
                print(f"  long-form visual {i+1}.{j+1}: animated {beat.get('visual_type')} graphic")
                continue
            clip_p = bd / f"lraw{i}_{j}.mp4"
            kws = beat["keywords"] or sec.get("keywords", ["documentary"])
            # Resume: a visual acquired for this exact prompt by an interrupted
            # attempt is reused instead of being searched/generated again.
            prompt_signature = stable_hash(prompt, beat.get("visual_type"), kws)
            visual_stage = f"visual_{i}_{j}"
            previous = (checkpoint.get(visual_stage) or {})
            previous_result = previous.get("result") or {}
            if (previous.get("status") == "done" and previous.get("input_hash") == prompt_signature
                    and previous_result.get("path") and not previous_result.get("fallback_card")
                    and Path(previous_result["path"]).is_file()
                    and Path(previous_result["path"]).stat().st_size > 1000
                    and os.getenv("AI_STUDIO_RESUME", "1").strip().lower() not in {"0", "false", "no"}):
                shot["_media"] = {"type": previous_result.get("type", "image"),
                                  "path": previous_result["path"]}
                fallback_state["reused"] += 1
                shot_timeline.append(shot)
                continue
            visual_mode = os.getenv("LONGFORM_VISUAL_MODE", "video_first").strip().lower()
            if visual_mode not in {"video_first", "image_first"}:
                raise ValueError("LONGFORM_VISUAL_MODE must be video_first or image_first.")
            # Long-form documentaries should use moving B-roll by default.
            # Images remain a fallback when no relevant stock clip is found.
            if visual_mode == "video_first":
                ai_video_mode = os.getenv("AI_VIDEO_MODE", "fallback").strip().lower()
                if ai_video_mode == "prefer" and generate_ai_video(prompt, clip_p, aspect_ratio="16:9"):
                    shot["_media"] = {"type": "video", "path": str(clip_p)}
                    print(f"  long-form visual {i+1}.{j+1}: AI-generated video (preferred mode)")
                elif fetch_clip(kws, clip_p, used_ids, scene_prompt=prompt):
                    shot["_media"] = {"type": "video", "path": str(clip_p)}
                    print(f"  long-form visual {i+1}.{j+1}: stock video")
                elif ai_video_mode != "prefer" and generate_ai_video(prompt, clip_p, aspect_ratio="16:9"):
                    shot["_media"] = {"type": "video", "path": str(clip_p)}
                    print(f"  long-form visual {i+1}.{j+1}: AI-generated video")
                elif generate_image(prompt, img_p, category=script.get("category", "general")):
                    shot["_media"] = {"type": "image", "path": str(img_p)}
                    print(f"  long-form visual {i+1}.{j+1}: generated image fallback")
                else:
                    if str(beat.get("visual_type") or "").lower() == "archival":
                        from core.graphic_scenes import render_context_graphic
                        context_path = render_context_graphic(shot, shot_dur, bd, f"archival_context_{i}_{j}")
                        if context_path:
                            shot["_media"] = {"type": "image", "path": str(context_path)}
                            shot["visual_type"] = "context_graphic"
                            shot["archival_source_unavailable"] = True
                            shot["visual_disclosure"] = "Context graphic — not archival footage; no authentic archival asset was available."
                            shot["asset_provenance"] = {
                                "asset_type": "editorial_context_graphic",
                                "archival_authenticity_status": "not_archival",
                                "disclosure_required": True,
                            }
                            print(f"  long-form visual {i+1}.{j+1}: disclosed context graphic (not archival)")
                        else:
                            raise RuntimeError(
                                f"Archival source unavailable and context graphic failed for chapter {i+1}, beat {j+1}."
                            )
                    else:
                        card_p = bd / f"lcard{i}_{j}.jpg"
                        if _fallback_card(card_p, beat.get("anchor") or narration, size=(W_L, H_L),
                                          kicker=str(sec.get("title") or f"Chapter {i+1}")[:40],
                                          index=i, total=len(sections),
                                          category=script.get("category", "general"),
                                          keywords=beat.get("keywords") or None):
                            shot["_media"] = {"type": "image", "path": str(card_p)}
                            shot["visual_type"] = "context_graphic"
                            shot["visual_disclosure"] = "Original explainer graphic; no scene-matched footage or image was available."
                            fallback_state["cards"].append(str(card_p))
                            print(f"  long-form visual {i+1}.{j+1}: disclosed explainer card (no provider responded)")
                        else:
                            raise RuntimeError(
                                f"No scene-matched video or image was available for long-form chapter {i+1}, beat {j+1}. "
                                "Configure a visual provider; refusing unrelated filler."
                            )
            else:
                if generate_image(prompt, img_p, category=script.get("category", "general")):
                    shot["_media"] = {"type": "image", "path": str(img_p)}
                elif fetch_clip(kws, clip_p, used_ids, scene_prompt=prompt):
                    shot["_media"] = {"type": "video", "path": str(clip_p)}
                else:
                    card_p = bd / f"lcard{i}_{j}.jpg"
                    if str(beat.get("visual_type") or "").lower() != "archival" and _fallback_card(
                            card_p, beat.get("anchor") or narration, size=(W_L, H_L),
                            kicker=str(sec.get("title") or f"Chapter {i+1}")[:40], index=i,
                            total=len(sections), category=script.get("category", "general"),
                            keywords=beat.get("keywords") or None):
                        shot["_media"] = {"type": "image", "path": str(card_p)}
                        shot["visual_type"] = "context_graphic"
                        shot["visual_disclosure"] = "Original explainer graphic; no scene-matched footage or image was available."
                        fallback_state["cards"].append(str(card_p))
                    else:
                        raise RuntimeError(
                            f"No scene-matched visual was available for long-form chapter {i+1}, beat {j+1}. "
                            "Image generation and relevant stock search both failed; refusing unrelated footage."
                        )
            checkpoint.mark(visual_stage, "done", input_hash=prompt_signature, result={
                "type": shot["_media"]["type"], "path": str(shot["_media"]["path"]),
                "fallback_card": str(shot["_media"]["path"]) in fallback_state["cards"],
            })
            shot_timeline.append(shot)

    # Optional second-pass Wan2.1 handoff: mapped review shots replace the
    # corresponding generated visuals before the EDL, review manifest and render.
    wan_review_path = os.getenv("WAN_MAPPED_REVIEW_REPORT", "").strip()
    if wan_review_path:
        from core.wan_timeline_adapter import apply_mapped_wan_review
        wan_result = apply_mapped_wan_review(shot_timeline, wan_review_path)
        print(
            f"Wan2.1 timeline adapter: {wan_result['applied_count']} clips applied; "
            "all mapped clips remain pending human review and rights clearance."
        )

    if not any(item.get("_media", {}).get("path") for item in shot_timeline):
        raise RuntimeError("No usable long-form visuals were generated. Configure an image provider or add licensed clips.")
    if fallback_state["reused"]:
        print(f"Resume: reused {fallback_state['reused']} visuals acquired by an earlier attempt.")
    from core import explainer_cards
    fallback_report = explainer_cards.write_fallback_report(
        bd, [item.get("_media", {}).get("path") for item in shot_timeline
             if item.get("_media", {}).get("path") and not item.get("presenter_mode")
             and item.get("visual_type") != "chapter_title"],
        explainer_cards.max_share(0.35))

    # Add a reviewable per-shot cinema plan and pass camera controls into FFmpeg rendering.
    shot_timeline[:] = plan_cinematic_shots(shot_timeline, defaults={"aspect_ratio": "16:9"})
    write_cinematic_shot_plan(shot_timeline, bd / "cinematic_shot_plan.json", defaults={"aspect_ratio": "16:9"})

    # A presenter is only a brief on-camera introduction. Reject renders where
    # presenter clips consume the chapter and leave no room for documentary B-roll.
    visual_coverage = validate_documentary_visual_coverage(shot_timeline, total)
    # Each chapter's shots must add up to that chapter's narration; otherwise the
    # pictures slide against the voice for the rest of the film.
    drift = _timeline_drift(shot_timeline, sections)
    visual_coverage["max_chapter_drift_seconds"] = round(drift, 3)
    if drift > 0.25:
        print(f"WARNING: visual timeline differs from narration by up to {drift:.2f}s in a chapter.")
    (bd / "visual_coverage_report.json").write_text(
        json.dumps(visual_coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "Documentary visual coverage: "
        f"{visual_coverage['non_presenter_ratio']:.1%} non-presenter visuals, "
        f"{visual_coverage['presenter_ratio']:.1%} presenter."
    )

    # Produce a deterministic editorial EDL before final rendering so every
    # shot, music cue, overlay and missing visual is reviewable alongside the MP4.
    try:
        from core.documentary_edl import build_edit_decision_list
        tone = str(script.get("tone") or script.get("category") or "investigative")
        requested_runtime = script.get("runtime_minutes") or script.get("target_runtime_minutes")
        edl = build_edit_decision_list(
            shot_timeline, sections, total, tone=tone,
            target_platform="YouTube 16:9",
            requested_runtime_minutes=float(requested_runtime) if requested_runtime else None,
        )
        edl_path = bd / "edit_decision_list.json"
        edl_path.write_text(json.dumps(edl, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"Documentary EDL: {len(edl['timeline'])} timeline events, "
            f"{len(edl['music'])} chapter music cues, "
            f"{len(edl['missing_asset_requests'])} missing-asset/review requests."
        )
        for warning in edl["qa_report"]["fixes_applied"]:
            print(f"EDL QA: {warning}")
    except Exception as exc:
        print(f"ERROR: documentary EDL generation failed: {exc}")
        raise RuntimeError("Documentary edit-decision list is required for editorial review.") from exc

    # Save a reviewable shot manifest and sample frames before final rendering.
    try:
        import json
        from core.visual_production import build_visual_review
        from core.visual_consistency import audit_visual_consistency
        visual_report = build_visual_review(
            shot_timeline, bd,
            research_sources=script.get("sources") or script.get("research_sources", [])
        )
        consistency_report = audit_visual_consistency(visual_report, bd)
        from core.asset_licensing import build_licence_register
        licence_report = build_licence_register(shot_timeline, bd)
        visual_report["licence_register"] = licence_report
        (bd / "visual_review_report.json").write_text(
            json.dumps(visual_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"Asset rights register: {licence_report['asset_count']} assets; "
            f"{licence_report['eligible_pending_human_review']} eligible pending human review; "
            f"{licence_report['blocked_count']} blocked."
        )
        for item in licence_report["blocked_assets"]:
            print(f"ASSET RIGHTS BLOCKED: {item['shot_id']}: {item['reason']}")
        # Append verified asset credits to the YouTube description metadata.
        release_path = bd / "release_metadata.json"
        if release_path.is_file():
            release_data = json.loads(release_path.read_text(encoding="utf-8"))
            description = str(release_data.get("description") or "").strip()
            credits_text = Path(licence_report["credits_path"]).read_text(encoding="utf-8").strip()
            release_data["description"] = (description + "\n\n" + credits_text).strip()
            release_data["asset_licence_register"] = "licenses.csv"
            release_path.write_text(json.dumps(release_data, ensure_ascii=False, indent=2), encoding="utf-8")
        quality_by_id = {row["shot_id"]: row for row in consistency_report.get("shots", [])}
        for shot in visual_report.get("shots", []):
            shot["visual_quality"] = quality_by_id.get(shot.get("shot_id"), {"status": "unavailable"})
        visual_report["visual_consistency_summary"] = consistency_report.get("summary", {})
        (bd / "visual_review_report.json").write_text(
            json.dumps(visual_report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Visual review: {visual_report['shot_count']} shots; human approval required.")
        print(f"Visual consistency: {consistency_report['status']} ({consistency_report['summary']['measured_shot_count']} measured shots).")
        for issue in consistency_report.get("issues", []):
            print(f"VISUAL QUALITY {issue['level'].upper()}: {issue['message']}")
        for issue in visual_report.get("issues", []):
            print(f"VISUAL REVIEW {issue['level'].upper()}: {issue['message']}")
    except Exception as exc:
        print(f"ERROR: visual review report could not be generated: {exc}")
        raise RuntimeError("Documentary visual-review and licensing evidence are required; refusing an unreviewable render.") from exc

    timings = subtitle_timings
    subtitle_sections = [dict(sec, subtitle_title=subtitle_titles[i]) for i, sec in enumerate(sections)]
    ass = bd / "subs.ass"
    slate_windows = [(float(shot.get("_start", 0.0)), float(shot.get("_start", 0.0)) + float(shot.get("_dur", 0.0)))
                     for shot in shot_timeline if shot.get("visual_type") == "chapter_title"]
    _make_ass_longform(timings, subtitle_sections, total, ass, disclaimer, quiet_windows=slate_windows)
    try:
        from core.subtitles_export import write_sidecars
        from core.fonts import contains_devanagari
        caption_language = "hi" if any(contains_devanagari(text) for _, _, text in timings) else "en"
        write_sidecars(timings, bd, "captions", caption_language)
    except Exception as exc:
        print(f"WARNING: SRT/VTT caption sidecars could not be written: {exc}")

    music = _pick_music()
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = cfg.OUTPUT_DIR / f"longform_{job_id}.mp4"
    credits_file = bd / "credits.txt"
    checkpoint.mark("render", "running")
    # A persistent work directory lets an interrupted render reuse finished segments.
    lf_render(shot_timeline, voice_path, ass, total, out, music,
              credits_path=credits_file if credits_file.is_file() else None,
              work_dir=bd / "render_work")
    checkpoint.mark("render", "done", files=[str(out)])
    _quality_check(job_id, out, ass, bd, documentary=True,
                   body_seconds=total if credits_file.is_file() else None)
    gated = _apply_fallback_gate(bd / "quality_report.json", fallback_report)
    if gated:
        update_video(job_id, quality_score=gated["score"], quality_status=gated["status"])
    checkpoint.mark("quality", "done")
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


def _record_failures(function):
    """Persist the failure reason on the job row so the dashboard/CLI can show it."""
    import functools

    @functools.wraps(function)
    def wrapper(script, job_id, *args, **kwargs):
        try:
            return function(script, job_id, *args, **kwargs)
        except BaseException as exc:
            try:
                update_video(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
            except Exception:
                pass
            raise
    return wrapper


build_longform = _record_failures(build_longform)

# Record one event per complete render operation.
from core.monitoring import monitored as _monitored
from core.ffmpeg_utils import PROBE_TIMEOUT, ffprobe_bin
build_shorts = _monitored("pipeline", "build_shorts", build_shorts)
build_longform = _monitored("pipeline", "build_longform", build_longform)
