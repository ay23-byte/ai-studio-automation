"""Main pipeline: takes a script dict, builds a video, returns output path."""
import random, re, subprocess, os, time
from pathlib import Path
from core import config as cfg
from core.voice import synthesise
from core.images import generate_image, fetch_clip, generate_ai_video
from core.db import save_video, update_video
from core.cinematic_shot_planner import plan_cinematic_shots, write_cinematic_shot_plan

from core import ffmpeg_utils
from core.jobs import Checkpoint, stable_hash
from core.source_evidence import research_source_records

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


def robotic_narration_problem(build_dir):
    """Why this narration can never be published, or ``None``.

    When the voice provider does not answer, the narration falls back to the offline robotic
    voice so that short formats still get a file. Audio QA always refuses a documentary with
    that voice, so building the rest of it (two hours in production build 43702915253) only
    delays the same refusal. ``LONGFORM_ALLOW_ROBOTIC_NARRATION=1`` keeps the old behaviour.
    """
    if os.getenv("LONGFORM_ALLOW_ROBOTIC_NARRATION", "").strip().lower() in ("1", "true", "yes"):
        return None
    from core.review_packaging import ROBOTIC_ENGINES, narration_metadata
    narration = narration_metadata(build_dir)
    engine = str(narration.get("provider") or "").lower()
    if not (narration.get("fallback_used") and engine in ROBOTIC_ENGINES):
        return None
    return (f"Narration failed, no natural voice was available: {narration.get('fallback_reason') or 'no reason recorded'} "
            f"Only the robotic offline voice ({engine}) was left. Audio QA never accepts it, so the build "
            "stops here instead of rendering a film that would be refused. Start a new run; if the same "
            "phrase stops every voice again, the wording of that phrase is the cause.")


def _voice_stage(checkpoint, text, bd, language, gender, refuse_robotic=False):
    """Narration with checkpoint reuse: identical text/voice is never re-synthesised."""
    import json
    timings_path = Path(bd) / "voice_timings.json"
    voice_path = Path(bd) / "voice.wav"
    signature = stable_hash(text, language, gender, os.getenv("TTS_ENGINE", "edge"),
                            os.getenv("TTS_VOICE", ""), os.getenv("TTS_PRONUNCIATION_MAP", ""))

    def run():
        path, timings = synthesise(text, bd, language=language, gender=gender)
        if refuse_robotic:
            # Raised inside the checkpointed step, so a later run speaks the narration again.
            problem = robotic_narration_problem(bd)
            if problem:
                raise RuntimeError(problem)
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


def measured_runtime_report(script, narration, narration_seconds):
    """Compare measured narration with both the lower and upper runtime bounds.

    The report is checked before any footage is fetched. The lower bound prevents
    under-length films; the upper bound prevents expensive renders that cannot meet
    the requested duration. Set LONGFORM_MAX_MEASURED_RUNTIME_FRACTION to tune the
    upper tolerance (default 1.10).
    """
    words = len(str(narration or "").split())
    seconds = float(narration_seconds or 0)
    target = (script.get("runtime_validation") or {}).get("target_minutes")
    try:
        floor = float(os.getenv("LONGFORM_MIN_MEASURED_RUNTIME_FRACTION", "0.80") or 0.80)
    except ValueError:
        floor = 0.80
    try:
        ceiling = float(os.getenv("LONGFORM_MAX_MEASURED_RUNTIME_FRACTION", "1.10") or 1.10)
    except ValueError:
        ceiling = 1.10
    floor = min(1.0, max(0.0, floor))
    ceiling = max(0.0, ceiling)
    if ceiling < floor:
        raise ValueError("LONGFORM_MAX_MEASURED_RUNTIME_FRACTION must be >= the minimum runtime fraction.")
    report = {
        "target_minutes": target, "narration_seconds": round(seconds, 3),
        "narration_minutes": round(seconds / 60.0, 2), "words": words,
        "words_per_minute": round(words / seconds * 60.0, 1) if seconds > 0 else 0.0,
        "minimum_fraction": floor, "maximum_fraction": ceiling,
        "fraction_of_target": None, "status": "NOT_APPLICABLE", "issue": None,
    }
    if target:
        fraction = seconds / (float(target) * 60.0)
        report["fraction_of_target"] = round(fraction, 4)
        if fraction < floor:
            report["status"] = "FAIL"
            report["issue"] = (
                f"The narration runs {seconds / 60.0:.1f} minutes, {fraction:.0%} of the requested "
                f"{float(target):g} minutes (at least {floor:.0%} is required). The script has {words} words, "
                f"spoken at {report['words_per_minute']:.0f} words a minute. "
                "Refusing to render an under-length documentary.")
        elif fraction > ceiling:
            report["status"] = "FAIL"
            report["issue"] = (
                f"The narration runs {seconds / 60.0:.1f} minutes, {fraction:.0%} of the requested "
                f"{float(target):g} minutes (maximum {ceiling:.0%} is allowed). The script has {words} words, "
                f"spoken at {report['words_per_minute']:.0f} words a minute. "
                "Refusing to fetch footage or render an over-length documentary; shorten the narration and retry.")
        else:
            report["status"] = "PASS"
    return report


def max_shot_seconds():
    """Longest time one B-roll picture may stay on screen: ``LONGFORM_MAX_SHOT_SECONDS`` (12, 0 = no limit)."""
    try:
        value = float(os.getenv("LONGFORM_MAX_SHOT_SECONDS", "12") or 12)
    except ValueError:
        value = 12.0
    return 0.0 if value <= 0 else max(4.0, value)


def split_long_beat(shot, acquire, limit_seconds, max_parts=4):
    """Cover one long narration beat with several different pictures instead of one.

    A storyboard beat can last 30-40 seconds in a long documentary; one clip looping for that
    long reads as a slideshow. The beat keeps its narration, prompt and search terms, and
    ``acquire(part_number)`` is asked for another asset ({"type", "path"}) for each further
    part. When no further asset exists the last picture found simply stays for the rest of
    the beat, so the timeline never has a gap. Returns the shots in order; their durations
    add up to the beat's original duration.
    """
    duration = float(shot.get("_dur") or 0.0)
    if not limit_seconds or duration <= limit_seconds:
        return [shot]
    import math
    parts = min(int(max_parts), int(math.ceil(duration / float(limit_seconds))))
    if parts <= 1:
        return [shot]
    part_seconds = duration / parts
    start = float(shot.get("_start") or 0.0)
    shots = [shot]
    for number in range(2, parts + 1):
        try:
            media = acquire(number)
        except Exception as exc:
            print(f"  additional picture {number} for this beat failed: {type(exc).__name__}: {exc}")
            media = None
        if not media:
            break
        extra = dict(shot)
        extra["_media"] = media
        extra["beat_part"] = number
        if extra.get("title"):
            extra["title"] = ""            # the chapter heading belongs to the first picture only
        shots.append(extra)
    shot["beat_part"] = 1
    for index, item in enumerate(shots):
        item["_start"] = start + index * part_seconds
        item["_dur"] = part_seconds
        item["beat_parts"] = len(shots)
    shots[-1]["_dur"] = duration - part_seconds * (len(shots) - 1)
    return shots


def repair_unusable_pictures(shot_timeline, replace, check=None, max_attempts=3):
    """Replace, before the edit, every B-roll picture that the picture audit would refuse.

    The audit of the finished film cannot be overridden, and it runs after everything else, so
    one black clip used to cost the whole build. Here each shot that carries ``_picture_repair``
    (B-roll found or generated for a storyboard beat) is measured with the audit's own rule on
    the seconds it shows. ``replace(shot, problem, attempt)`` returns another asset
    ({"type", "path"}, optionally ``"project_graphic": True`` for a card drawn by this project,
    which is not measured again) or None when nothing else exists.

    Returns one record per shot that had a problem; ``"resolved"`` says whether it was replaced.
    """
    if check is None:
        from core.visual_consistency import media_picture_problem

        def check(path, seconds):
            return media_picture_problem(path, window_seconds=seconds, dark_margin=0.0, check_size=True)

    records = []
    for index, shot in enumerate(shot_timeline):
        media = shot.get("_media") or {}
        if not shot.get("_picture_repair") or not media.get("path") or media.get("project_graphic"):
            continue
        problem = check(media["path"], shot.get("_dur"))
        if not problem:
            continue
        record = {"shot": index + 1, "problem": problem, "original": str(media["path"]),
                  "attempts": 0, "resolved": False, "replacement": None}
        while problem and record["attempts"] < max_attempts:
            record["attempts"] += 1
            try:
                new_media = replace(shot, problem, record["attempts"])
            except Exception as exc:
                print(f"  replacement {record['attempts']} for shot {index + 1} failed: {type(exc).__name__}: {exc}")
                new_media = None
            if not new_media:
                continue
            shot["_media"] = new_media
            record["replacement"] = str(new_media.get("path"))
            problem = None if new_media.get("project_graphic") else check(new_media["path"], shot.get("_dur"))
        record["resolved"] = not problem
        if problem:
            record["remaining_problem"] = problem
        records.append(record)
    return records


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
    """Use only local music with a provenance sidecar documenting its licence and URL.

    Unverified files are ignored; the renderer supplies an original generated score
    instead of silently using a track whose rights cannot be established.
    """
    import json
    candidates = list(cfg.MUSIC_DIR.glob("*.mp3")) + list(cfg.MUSIC_DIR.glob("*.wav"))
    eligible = []
    for path in candidates:
        sidecar = path.with_suffix(path.suffix + ".source.json")
        try:
            metadata = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
        except (OSError, ValueError):
            continue
        has_licence = bool(metadata.get("licence") or metadata.get("license"))
        has_url = bool(metadata.get("licence_url") or metadata.get("license_url"))
        if has_licence and has_url:
            eligible.append(path)
    return random.choice(eligible) if eligible else None

def _local_clips():
    files = [p for p in cfg.CLIPS_DIR.iterdir() if p.suffix.lower() in (".mp4",".mov",".webm")]
    random.shuffle(files); return files

def _ts(s):
    cs = int(round(max(s,0)*100))
    return f"{cs//360000}:{(cs//6000)%60:02d}:{(cs//100)%60:02d}.{cs%100:02d}"

def _clean(s): return s.replace("{","(").replace("}",")")


def _scene_search_brief(beat: dict, keywords) -> str:
    """Build a compact, subject-led metadata query for archive and stock catalogues.

    Provider metadata is usually a short title/tag list, not a full description of
    the storyboard. Comparing it with a long cinematic brief dilutes real matches:
    a correct clip can share the subject but fail the 0.30 token-coverage gate.
    Keep only distinctive anchors; vision screening and the independent relevance
    and licence gates still decide whether a downloaded clip may be selected.
    """
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "into", "about",
        "during", "before", "after", "while", "showing", "shows", "scene",
        "footage", "video", "documentary", "historical", "historic", "archival",
        "archive", "authentic", "genuine", "real", "period", "newsreel", "newsreels", "stock", "clip", "clips",
    }

    def terms(value, limit):
        result = []
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", str(value or "")):
            cleaned = word.strip("'-")
            if len(cleaned) < 3 or cleaned.lower() in stop or cleaned.lower() in {item.lower() for item in result}:
                continue
            result.append(cleaned)
            if len(result) >= limit:
                break
        return result

    # Prioritise the actual subject. Location and date are useful discriminators,
    # while keywords supply alternate names and concrete objects/actions.
    selected = []
    for value, limit in (
        (beat.get("subject", ""), 6),
        (beat.get("location", ""), 2),
        (beat.get("time_period", ""), 2),
        (" ".join(str(item) for item in (keywords or []) if str(item).strip()), 4),
    ):
        for token in terms(value, limit):
            if token not in selected:
                selected.append(token)
    return " ".join(selected[:12])[:600]


def _short_scene_search_brief(scene: dict) -> str:
    """Use the scene's visual direction and stock keywords, not narration prose."""
    keywords = scene.get("keywords") or []
    keyword_text = " ".join(str(item).strip() for item in keywords if str(item).strip())
    image = " ".join(str(scene.get("image") or "").split())
    if not image and not keyword_text:
        image = " ".join(str(scene.get("say") or "").split())
    return " ".join(part for part in (image, keyword_text) if part)[:400]

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
    """Prefer relevant real footage, then optional AI video, then scene-matched stills."""
    img_p = bd / f"img{index}.jpg"
    clip_p = bd / f"raw{index}.mp4"
    card_p = bd / f"card{index}.jpg"
    keywords = scene.get("keywords") or [str(scene.get("say", ""))[:100]]
    scene_search_prompt = _short_scene_search_brief(scene)
    scene_prompt = (
        "Create a visual that directly illustrates this exact spoken narration. "
        "Do not use unrelated generic filler. Spoken line: "
        + str(scene.get("say", "")) + ". Visual direction: "
        + str(scene.get("image") or scene.get("say", ""))
        + ". 9:16 cinematic documentary image, accurate subject and action, no text or logos."
    )

    # News should feel like news: try relevant, licensed moving footage before
    # spending time on still-image generation. The footage selector enforces
    # relevance, licence and media validation; it is never allowed to invent
    # footage of a specific event.
    if fetch_clip(keywords, clip_p, used_ids, scene_prompt=scene_search_prompt):
        return {"type": "video", "path": clip_p, "provider": "stock"}

    has_local_video_model = bool(os.getenv("OPENVINO_LTX_MODEL_DIR", "").strip())
    if has_local_video_model and not state["ai_video_failed"]:
        if generate_ai_video(scene_prompt, clip_p, aspect_ratio="9:16"):
            return {"type": "video", "path": clip_p, "provider": "ai_video"}
        state["ai_video_failed"] = True

    # After repeated consecutive failures the image provider is down or rate
    # limited: stop paying its retry/back-off cost for every remaining scene.
    if state["image_failures"] < state["image_failure_limit"]:
        # Transient free-provider timeouts are common. Retry the exact scene before
        # falling back to a graphic card; each retry keeps the same subject and
        # changes only the composition request, never substitutes unrelated imagery.
        retries = max(0, min(3, int(os.getenv("SHORTS_IMAGE_GENERATION_RETRIES", "2") or 2)))
        for attempt in range(retries + 1):
            retry_prompt = scene_prompt if attempt == 0 else (
                scene_prompt + " Alternate cinematic camera angle and composition; preserve the exact subject, "
                "scientific accuracy, and action from the original direction."
            )
            if generate_image(retry_prompt, img_p, category=script.get("category", "general"),
                              search_query=scene_search_prompt):
                state["image_failures"] = 0
                return {"type": "image", "path": img_p, "provider": "image"}
            if attempt < retries:
                time.sleep(min(2 * (attempt + 1), 5))
        state["image_failures"] += 1
        if state["image_failures"] == state["image_failure_limit"]:
            print("  image providers failed after scene retries; skipping them for the remaining scenes.")
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
        from core import footage_report, footage_sources
        footage_sources.reset_run_state()
        footage_run = footage_report.begin_run(f"short job {job_id}")
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
            signature = stable_hash(sc.get("say"), sc.get("image"), sc.get("keywords"), cat, sc.get("visual_type"), os.getenv("PV2_ANIMATION_MODE", "off"), "animation-v1")
            record = checkpoint.get(f"visual_{i}") or {}
            stored = record.get("result") or {}
            was_card = stored.get("provider") == "fallback_card"
            retry_cards = os.getenv("RETRY_FALLBACK_CARDS_ON_RESUME", "1").strip().lower() in {"1", "true", "yes"}

            def acquire(i=i, sc=sc):
                # Daily News and generic/physics Shorts share this renderer.
                # Only explicitly diagram/animation scenes with a supported,
                # positively stated concept are routed to Manim or Blender.
                from core.animation_integration import render_animation
                animation = render_animation(
                    narration=sc.get("say", ""),
                    visual_type=sc.get("visual_type") or sc.get("asset_type") or sc.get("kind", ""),
                    visual_prompt=sc.get("image") or sc.get("visual_prompt", ""),
                    output_dir=bd, scene_id=f"short_{i + 1:03d}",
                    content_type="physics_short",
                )
                if animation:
                    return {"type": animation["type"], "path": str(animation["path"]),
                            "provider": animation["provider"]}
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
        from core.subtitle_translation import shorts_subtitle_language, translate_short_subtitles
        subtitle_target = shorts_subtitle_language(os.getenv("SHORTS_SUBTITLE_LANGUAGE", "english"))
        caption_items, labels, hook, cta, subtitle_report = translate_short_subtitles(
            caption_items, labels, hook, cta, target_language=subtitle_target,
            source_language=language)
        (bd / "subtitle_translation.json").write_text(
            json.dumps(subtitle_report, ensure_ascii=False, indent=2), encoding="utf-8")
        ass = bd / "subs.ass"
        _make_ass_shorts(caption_items, hook, cta, total, labels, ass, disclaimer)
        try:
            from core.subtitles_export import write_sidecars
            sidecar_language = "en" if subtitle_target == "english" else str(language).lower()
            write_sidecars(caption_items, bd, "captions", sidecar_language)
        except Exception as exc:
            print(f"WARNING: SRT/VTT caption sidecars could not be written: {exc}")

        # Render
        music = _pick_music()
        cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = cfg.OUTPUT_DIR / f"shorts_{job_id}.mp4"
        if footage_run.scenes:
            try:
                footage_run.write(bd)
            except OSError as exc:
                print(f"WARNING: footage provider report could not be written: {exc}")
        checkpoint.mark("render", "running")
        shorts_render(media, lens, cuts, voice_path, ass, total, out, music, work_dir=bd / "render_work")
        checkpoint.mark("render", "done", files=[str(out)])
        _quality_check(job_id, out, ass, bd)
        # Machine-measured audio evidence is required by the autonomous private-release gate.
        # This does not claim a human listening review; it measures loudness, true peak,
        # clipping and long silences independently of the video-level QA report.
        try:
            from core.audio_quality import inspect_audio
            audio_report = inspect_audio(out)
        except Exception as exc:
            audio_report = {"status": "FAIL", "issues": [
                f"Automated audio metering could not complete: {type(exc).__name__}: {str(exc)[:300]}"],
                "warnings": [], "human_review_status": "pending"}
        (bd / "audio_quality_report.json").write_text(
            json.dumps(audio_report, ensure_ascii=False, indent=2), encoding="utf-8")
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
    voice_path, timings = _voice_stage(checkpoint, full_narration, bd, language, voice_gender,
                                       refuse_robotic=True)
    total = _media_seconds(voice_path)
    # The script gate counts words; this one measures the narration that was actually
    # synthesised, before any visual is fetched or rendered.
    runtime_report = measured_runtime_report(script, full_narration, total)
    import json as _json
    (bd / "runtime_report.json").write_text(_json.dumps(runtime_report, indent=2), encoding="utf-8")
    print(f"Measured narration: {runtime_report['narration_minutes']:.1f} min, "
          f"{runtime_report['words']} words, {runtime_report['words_per_minute']:.0f} words a minute.")
    if runtime_report["status"] == "FAIL":
        raise RuntimeError(runtime_report["issue"])

    # Chapter boundaries follow the measured narration (phrase timings from the
    # voice stage) instead of a word-count estimate, so chapter slates, lower
    # thirds and visuals change exactly when each chapter is spoken.
    from core.timing import align_units
    used_ids = set()
    # One footage report per documentary: which providers answered, what was
    # rejected and why, and which scenes still need a person.
    from core import footage_report, footage_sources
    footage_sources.reset_run_state()
    footage_sources.set_run_subject(script.get("title"), script.get("topic"), script.get("category"))
    # The finished film is refused when a source is smaller than the visual audit's floor, so
    # refuse such a clip here, while the next candidate can still take its place.
    from core import visual_consistency
    os.environ.setdefault("FOOTAGE_MIN_WIDTH", str(visual_consistency.MIN_SOURCE_WIDTH))
    os.environ.setdefault("FOOTAGE_MIN_HEIGHT", str(visual_consistency.MIN_SOURCE_HEIGHT))
    footage_run = footage_report.begin_run(f"long-form job {job_id}")
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
    from core.subtitle_translation import (
        documentary_subtitle_language, english_subtitle_report, translate_longform_subtitles,
    )
    # English subtitles only: Hindi, Hinglish or as-spoken subtitles are refused here.
    subtitle_target = documentary_subtitle_language(
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
        english_check = None
        if subtitle_target == "english":
            # Whatever produced the text (translation, or the narration as spoken), it is
            # only burned in and exported if it really is English.
            english_check = english_subtitle_report([text for _, _, text in lines] + list(titles))
            if english_check["status"] != "PASS":
                raise RuntimeError(
                    "Subtitles are not English and were not rendered: " + " ".join(english_check["issues"])
                    + (" The translation failed and the spoken text is not English." if mode == "source_fallback" else "")
                )
        translation_path.write_text(json.dumps(
            {"target": subtitle_target, "mode": mode, "english_check": english_check,
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
    source_evidence_capture_records = []
    source_evidence_required_beat_failures = []
    source_evidence_cache = {}
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
            from core.presenter_quality import (
                analyse_presenter_motion, inspect_presenter_clip, read_presenter_metadata,
                read_render_record, write_presenter_metadata,
            )
            # The narration slice the clip was rendered from, when it is on record. Comparing
            # the clip with its own length proves nothing.
            recorded = read_presenter_metadata(avatar_clip)
            render_record = read_render_record(avatar_clip)
            expected_duration = recorded.get("narration_segment_seconds")
            validation = inspect_presenter_clip(
                avatar_clip, expected_duration=expected_duration,
                tolerance_seconds=0.75 if expected_duration is None else 0.25, require_audio=True,
                ffprobe=ffprobe_bin(),
            )
            if validation["status"] == "FAIL":
                raise ValueError(
                    f"Talking Avatar clip for chapter {i + 1} failed A/V validation: "
                    + "; ".join(validation["issues"])
                )
            # Count the frames that are really different and check the mouth moves with the
            # voice. A file that states 30 fps but repeats one picture is not accepted.
            motion = analyse_presenter_motion(
                avatar_clip, mouth_box=render_record.get("mouth_box"),
                mouth_track=render_record.get("mouth_open_per_frame"),
                ffmpeg=ffmpeg_bin(), ffprobe=ffprobe_bin(),
            )
            if motion["status"] == "FAIL":
                raise ValueError(
                    f"Talking Avatar clip for chapter {i + 1} failed the motion check: "
                    + "; ".join(motion["issues"])
                )
            write_presenter_metadata(avatar_clip, {
                "provider": "AI Studio Avatar Studio (Talking Avatar SDK)",
                "asset_type": "ai_generated_video", "ai_generated": True,
                "model": "AI Studio Avatar Studio 3D renderer",
                "prompt": "3D virtual documentary presenter generated from the chapter narration audio; an illustrative host, not a real interview or documentary source.",
                "presenter_mode": "talking_avatar",
                "validation": validation, "motion": motion, "lip_sync_verified": False,
                "human_visual_review": "pending",
                "licence": "MIT",
                "licence_status": "Talking Avatar SDK and the built-in Microsoft Rocketbox avatars are MIT licensed; keep the licence notice with redistributed copies.",
                "notice": "AI-generated presenter footage. Frame, freeze and mouth-movement measurements do not prove phoneme-level lip-sync; a person must watch the clip before release.",
            })
            presenter_shot = dict(sec)
            presenter_shot["visual_type"] = "presenter"
            presenter_shot["subject"] = "3D virtual documentary presenter"
            presenter_shot["visual_prompt"] = (
                "3D virtual documentary presenter speaking directly to camera for the chapter introduction: "
                + str(sec.get("title") or "chapter introduction")
            )
            presenter_shot["_chapter_index"] = i
            presenter_shot["title"] = sec.get("title", "")
            presenter_shot["presenter_mode"] = "talking_avatar"
            measured = motion["status"] == "PASS" and validation["status"] == "PASS"
            presenter_shot["presenter_sync_status"] = (
                "motion_measured_human_review_pending" if measured else "duration_aligned_review"
            )
            presenter_shot["presenter_motion"] = {
                key: motion.get(key) for key in (
                    "status", "distinct_frame_rate", "longest_frozen_seconds", "repeated_frame_ratio",
                    "voiced_mouth_motion_ratio", "mouth_track_offset_frames", "mouth_measured", "warnings",
                )
            }
            presenter_shot["presenter_human_review"] = "pending"
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
                title_text = str(sec.get("title") or f"Chapter {i + 1}")
                title_shot = dict(sec)
                title_shot.update({
                    "_chapter_index": i,
                    "visual_type": "chapter_title",
                    "subject": "Animated documentary chapter title slate",
                    "visual_prompt": (
                        "A short animated documentary chapter title slate displaying the chapter heading: "
                        + title_text + ". This is an intentional editorial title card, not B-roll."
                    ),
                    "narration": "Chapter title: " + title_text,
                    "on_screen_text": title_text,
                    "_start": shot_cursor, "_dur": title_duration,
                    "_media": {"type": "video", "path": str(title_clip)},
                })
                shot_timeline.append(title_shot)
                shot_cursor += title_duration
                durations = slate_durations
        # Evidence screenshots need a readable on-screen interval. Rebalance
        # within this chapter instead of silently skipping a cited source when
        # its anchor happened to receive a very short beat allocation.
        from core.source_evidence import allocate_evidence_beat_durations
        durations = allocate_evidence_beat_durations(beats, durations)
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
            # For data, timeline and document beats, briefly show a genuine
            # screenshot of the exact cited source page at the matching narration moment.
            # The screenshot is never generated or substituted with a fake article image.
            evidence_kind = str(beat.get("visual_type") or "").strip().lower()
            if evidence_kind in {"chart", "timeline", "document", "evidence"} or beat.get("chart_spec") or beat.get("timeline_events"):
                try:
                    from core.source_evidence import cited_source_records, capture_source_screenshot
                    cited = cited_source_records(beat, research_source_records(script))
                    explicit_refs = list(beat.get("source_refs") or [])
                    explicit_refs.extend((beat.get("chart_spec") or {}).get("source_refs") or [])
                    for event in beat.get("timeline_events") or []:
                        explicit_refs.extend(event.get("source_refs") or [])
                    beat_screenshot_captured = False
                    if explicit_refs and not cited:
                        failure = {
                            "status": "FAILED",
                            "reason": "No valid HTTP(S) research source matched the evidence beat's source_refs.",
                            "source_refs": list(dict.fromkeys(str(ref) for ref in explicit_refs)),
                        }
                        source_evidence_capture_records.append(failure)
                        source_evidence_required_beat_failures.append(failure)
                    for source in cited:
                        source_id = source["source_id"]
                        if source_id not in source_evidence_cache:
                            captured = capture_source_screenshot(
                                source, bd / "source_evidence_screenshots"
                            )
                            source_evidence_cache[source_id] = captured
                            source_evidence_capture_records.append(captured)
                        captured = source_evidence_cache[source_id]
                        if captured.get("status") != "CAPTURED":
                            continue
                        evidence_duration = min(4.0, float(shot_dur) * 0.30)
                        if evidence_duration < 1.25 or float(shot_dur) - evidence_duration < 1.0:
                            continue
                        evidence_shot = dict(shot)
                        evidence_shot.update({
                            "_start": float(shot["_start"]),
                            "_dur": evidence_duration,
                            "visual_type": "source_evidence_screenshot",
                            "subject": source.get("publisher") or "Original source",
                            "visual_prompt": "Real webpage screenshot from the cited original source; show the actual article/report page, not a generated imitation.",
                            "visual_disclosure": "Original source webpage screenshot captured during documentary production; capture time and source URL are recorded in the source sidecar.",
                            "source_screenshot": captured,
                            "source_refs": [source_id],
                            "asset_provenance": {
                                "asset_type": "source_webpage_screenshot",
                                "ai_generated": False,
                                "source_id": source_id,
                                "publisher": source.get("publisher"),
                                "source_url": source.get("url"),
                                "capture_metadata": captured.get("metadata_path"),
                            },
                            "_media": {"type": "image", "path": captured["path"]},
                        })
                        shot["_start"] = float(shot["_start"]) + evidence_duration
                        shot["_dur"] = float(shot["_dur"]) - evidence_duration
                        shot_timeline.append(evidence_shot)
                        beat_screenshot_captured = True
                        print(
                            f"  long-form source evidence {i+1}.{j+1}: real webpage screenshot "
                            f"from {source.get('publisher')} ({source_id})"
                        )
                        break
                    if cited and not beat_screenshot_captured:
                        failure = {
                            "status": "FAILED",
                            "reason": "No cited source webpage could be captured for this evidence beat.",
                            "source_refs": [row["source_id"] for row in cited],
                            "chapter": i + 1,
                            "beat": j + 1,
                        }
                        source_evidence_required_beat_failures.append(failure)
                        print(f"WARNING: no real source screenshot captured for evidence beat {i+1}.{j+1}.")
                except Exception as exc:
                    # Evidence-beat exceptions must remain visible to the strict
                    # screenshot gate; logging alone could silently let a cited
                    # chart/document beat through without its source-page capture.
                    failure = {
                        "status": "FAILED",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "source_refs": list(dict.fromkeys(str(ref) for ref in locals().get("explicit_refs", []) if str(ref).strip())),
                        "chapter": i + 1,
                        "beat": j + 1,
                    }
                    source_evidence_capture_records.append(failure)
                    source_evidence_required_beat_failures.append(failure)
                    print(f"WARNING: source webpage screenshot unavailable for beat {i+1}.{j+1}: {type(exc).__name__}: {exc}")

            # Use Manim/Blender for supported educational animation beats before
            # requesting footage. These clips are labelled conceptual illustrations.
            from core.animation_integration import render_animation
            animation = render_animation(
                narration=beat.get("anchor") or narration,
                visual_type=beat.get("visual_type", ""),
                visual_prompt=beat.get("prompt", ""),
                output_dir=bd, scene_id=f"documentary_{i + 1}_{j + 1}",
                content_type="documentary",
            )
            if animation:
                shot["_media"] = {"type": "video", "path": str(animation["path"])}
                shot["visual_type"] = "scientific_animation"
                shot["asset_provenance"] = {
                    "asset_type": "scientific_animation",
                    "provider": animation["provider"],
                    "automated_qa": animation["automated_qa"],
                    "renderer": animation["renderer"],
                    "source": str(animation["path"].with_suffix(animation["path"].suffix + ".source.json")),
                }
                shot_timeline.append(shot)
                print(f"  long-form visual {i+1}.{j+1}: {animation['renderer']} conceptual animation (automated QA PASS)")
                continue

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
            # Search with concise semantic anchors, not cinematic camera/lens prose.
            scene_search_brief = _scene_search_brief(beat, kws)
            shot["_picture_repair"] = {"keywords": list(kws), "brief": scene_search_brief, "chapter": i, "beat": j,
                                       "kicker": str(sec.get("title") or f"Chapter {i+1}")[:40],
                                       "card_keywords": beat.get("keywords") or None,
                                       "card_allowed": str(beat.get("visual_type") or "").lower() != "archival"}
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
                elif fetch_clip(kws, clip_p, used_ids, scene_prompt=scene_search_brief):
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
                elif fetch_clip(kws, clip_p, used_ids, scene_prompt=scene_search_brief):
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
            # A beat longer than the shot limit gets further, different pictures for the same
            # narration. Explainer cards and context graphics are not multiplied.
            is_substitute = (str(shot["_media"]["path"]) in fallback_state["cards"]
                             or shot.get("visual_type") == "context_graphic")

            def more_pictures(number, _i=i, _j=j, _kws=kws, _brief=scene_search_brief, _prompt=prompt):
                extra_clip = bd / f"lraw{_i}_{_j}_{number}.mp4"
                if fetch_clip(_kws, extra_clip, used_ids, scene_prompt=_brief):
                    return {"type": "video", "path": str(extra_clip)}
                extra_image = bd / f"limg{_i}_{_j}_{number}.jpg"
                angle = ("a different angle and framing of the same subject",
                         "a close-up detail of the same subject",
                         "a wide establishing view of the same setting")[(number - 2) % 3]
                if generate_image(_prompt + f" Show {angle}.", extra_image, category=script.get("category", "general")):
                    return {"type": "image", "path": str(extra_image)}
                return None

            beat_shots = [shot] if is_substitute else split_long_beat(shot, more_pictures, max_shot_seconds())
            if len(beat_shots) > 1:
                print(f"  long-form visual {i+1}.{j+1}: {shot_dur:.0f} s beat covered by {len(beat_shots)} pictures")
            shot_timeline.extend(beat_shots)

    source_evidence_report = {
        "capture_count": sum(1 for row in source_evidence_capture_records if row.get("status") == "CAPTURED"),
        "failure_count": sum(1 for row in source_evidence_capture_records if row.get("status") != "CAPTURED"),
        "captures": source_evidence_capture_records,
        "required_beat_failures": source_evidence_required_beat_failures,
        "notice": "Screenshots show the original source webpage as captured at build time. They are visual source context, not independent verification of the claims.",
    }
    (bd / "source_evidence_capture_report.json").write_text(
        json.dumps(source_evidence_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Source webpage screenshots: {source_evidence_report['capture_count']} captured, "
        f"{source_evidence_report['failure_count']} unavailable."
    )
    if (os.getenv("SOURCE_EVIDENCE_REQUIRE_SCREENSHOT", "0").strip().lower() in {"1", "true", "yes"}
            and source_evidence_required_beat_failures):
        failures = [
            f"{row.get('source_id') or row.get('source_refs') or 'source'}: {row.get('reason') or 'capture failed'}"
            for row in source_evidence_required_beat_failures
        ]
        raise RuntimeError(
            "Required real source-page screenshot evidence is unavailable for one or more data/document beats. "
            "Refusing a documentary that displays sourced claims without their original source-page screenshot: "
            + "; ".join(failures[:6])
        )

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

    # Measure every chosen B-roll picture with the final audit's rule and replace the ones it
    # would refuse, while replacing is still possible.
    def replace_picture(shot, problem, attempt):
        plan = shot["_picture_repair"]
        stem = f"{plan['chapter']}_{plan['beat']}_{int(shot.get('beat_part') or 1)}_{attempt}"
        if attempt < 3:
            clip = bd / f"lfix{stem}.mp4"
            if fetch_clip(plan["keywords"], clip, used_ids, scene_prompt=plan["brief"]):
                return {"type": "video", "path": str(clip)}
            return None
        card = bd / f"lcardfix{stem}.jpg"
        if plan["card_allowed"] and _fallback_card(
                card, shot.get("narration") or "", size=(W_L, H_L), kicker=plan["kicker"], index=plan["chapter"],
                total=len(sections), category=script.get("category", "general"), keywords=plan["card_keywords"]):
            shot["visual_type"] = "context_graphic"
            shot["visual_disclosure"] = "Original explainer graphic; the footage found for this moment was unusable."
            fallback_state["cards"].append(str(card))
            return {"type": "image", "path": str(card), "project_graphic": True}
        return None

    picture_repairs = repair_unusable_pictures(shot_timeline, replace_picture)
    (bd / "picture_repair_report.json").write_text(json.dumps({
        "checked_rule": "core.visual_consistency.picture_defects on the seconds each shot shows",
        "replaced": sum(1 for item in picture_repairs if item["resolved"]),
        "unresolved": sum(1 for item in picture_repairs if not item["resolved"]),
        "shots": picture_repairs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in picture_repairs:
        print(f"  picture check: shot {item['shot']} ({item['problem']}) "
              + ("replaced" if item["resolved"] else "could NOT be replaced"))
    unresolved_pictures = [item for item in picture_repairs if not item["resolved"]]
    if unresolved_pictures:
        raise RuntimeError(
            f"No usable footage for {len(unresolved_pictures)} shot(s): the picture found is unusable and no "
            "replacement exists (" + "; ".join(
                f"shot {item['shot']}: {item.get('remaining_problem') or item['problem']}"
                for item in unresolved_pictures[:6]) + "). Refusing to render a film that the picture audit would reject.")

    if not any(item.get("_media", {}).get("path") for item in shot_timeline):
        raise RuntimeError("No usable long-form visuals were generated. Configure an image provider or add licensed clips.")
    if fallback_state["reused"]:
        print(f"Resume: reused {fallback_state['reused']} visuals acquired by an earlier attempt.")
    from core import explainer_cards
    fallback_report = explainer_cards.write_fallback_report(
        bd, [item.get("_media", {}).get("path") for item in shot_timeline
             if item.get("_media", {}).get("path") and not item.get("presenter_mode")
             and item.get("visual_type") != "chapter_title"],
        explainer_cards.max_share(0.10))

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
        # A resumed build reuses acquired visuals and searches nothing; keep the
        # report written by the run that did the searching.
        if footage_run.scenes or not (bd / footage_report.REPORT_JSON).is_file():
            footage_summary = footage_run.write(bd)["summary"]
            visual_report["footage_provider_report"] = dict(footage_summary, file=footage_report.REPORT_JSON)
            print(
                f"Footage providers: {footage_summary['scenes_with_footage']} of "
                f"{footage_summary['scenes_searched']} searched scenes received validated footage from "
                f"{len(footage_summary['providers_with_validated_download'])} provider(s); "
                f"{footage_summary['scenes_needing_manual_review']} need manual review. "
                f"See {footage_report.REPORT_MARKDOWN}."
            )
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
from core.ffmpeg_utils import PROBE_TIMEOUT, ffmpeg_bin, ffprobe_bin
build_shorts = _monitored("pipeline", "build_shorts", build_shorts)
build_longform = _monitored("pipeline", "build_longform", build_longform)
