import hashlib, json, os, shutil, subprocess, tempfile
from pathlib import Path

from core import ffmpeg_utils
from core.render_join import reduce_segments, video_chain
from core.ffmpeg_utils import ffmpeg_bin  # honours FFMPEG_BINARY

W, H = 1920, 1080
FADE = 0.4
ENC = ["-c:v","libx264","-preset","medium","-crf","18","-profile:v","high",
       "-level","4.1","-pix_fmt","yuv420p","-r","30","-video_track_timescale","15360"]
TRANSITIONS = ["fade", "smoothleft", "fade", "circlecrop", "fade", "wipeleft"]
# Normalise full-range ("yuvj420p") stills/footage to broadcast range first.
RANGE = "scale=out_range=tv,format=yuv420p,"
# Keep documentary transitions restrained; reserve directional wipes for chapter changes.
TRANSITION_BY_TYPE = {
    "chart": "fade", "timeline": "fade", "document": "fade",
    "evidence": "fade", "interview": "smoothleft", "presenter": "fade",
    "map": "smoothleft", "archival": "fade", "ai_reconstruction": "fade",
}


def _transition_for(previous, current, index):
    prev_type = str(previous.get("visual_type") or "").lower()
    curr_type = str(current.get("visual_type") or "").lower()
    if previous.get("_chapter_index") != current.get("_chapter_index"):
        return "wipeleft"
    if curr_type in {"chart", "timeline", "document", "evidence"} or prev_type in {"chart", "timeline", "document", "evidence"}:
        return "fade"
    return TRANSITION_BY_TYPE.get(curr_type, TRANSITIONS[index % len(TRANSITIONS)])


def _pacing_duration(shot):
    """Bound individual B-roll beats to a documentary-friendly visual rhythm."""
    duration = max(float(shot.get("_dur", 8.0)), 0.1)
    kind = str(shot.get("visual_type") or "").lower()
    if kind in {"chart", "timeline", "document", "evidence", "map", "diagram"}:
        return min(max(duration, 4.0), 10.0)
    if kind in {"interview", "presenter"}:
        return min(max(duration, 5.0), 14.0)
    return min(max(duration, 3.5), 8.0)

def _video_motion_expressions(index):
    """Return deterministic, low-amplitude crop expressions for moving B-roll."""
    phase = (int(index) % 7) * 0.37
    x = f"(in_w-out_w)*(0.5+0.18*sin(t*0.12+{phase:.3f}))"
    y = f"(in_h-out_h)*(0.5+0.14*cos(t*0.10+{phase:.3f}))"
    return x, y


def _run(cmd): ffmpeg_utils.run(cmd)


def _audio_master_filter():
    """Build a bounded one-pass loudness master for the final documentary mix."""
    import os

    def setting(name, default, low, high):
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    target = setting("DOCUMENTARY_TARGET_LUFS", -16.0, -24.0, -8.0)
    peak = setting("DOCUMENTARY_TRUE_PEAK_DBFS", -1.5, -9.0, -0.1)
    lra = setting("DOCUMENTARY_TARGET_LRA", 11.0, 1.0, 20.0)
    return f"loudnorm=I={target}:TP={peak}:LRA={lra}"


def _seg(src, idx, dur, is_image, bd, shot=None):
    out = bd / f"lseg{idx}.mp4"
    if is_image:
        frames = max(int(dur*30),2)
        from core.cinematic_motion import still_motion_parameters
        z, x, y = still_motion_parameters((shot or {}).get("cinema_settings"), idx, frames)
        pos = f"x='{x}':y='{y}'"
        from core.visual_consistency import color_grade_filter
        grade = color_grade_filter()
        vf = (f"{RANGE}scale={W*2}:{H*2}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W*2}:{H*2},zoompan=z='{z}':{pos}:d=1:s={W}x{H}:fps=30,"
              f"{grade},format=yuv420p")
        _run([ffmpeg_bin(),"-y","-loglevel","error","-loop","1","-framerate","30",
              "-t",f"{dur:.3f}","-i",str(src),"-vf",vf,"-an",*ENC,str(out)])
    else:
        from core.visual_consistency import color_grade_filter
        grade = color_grade_filter()
        # Give moving B-roll a restrained, slow camera drift. The 6.7% overscan
        # prevents empty edges while the crop travels gently across the frame.
        from core.cinematic_motion import video_crop_expressions
        x_motion, y_motion = video_crop_expressions((shot or {}).get("cinema_settings"), idx, dur, 128, 72)
        vf = (f"{RANGE}scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W}:{H},scale=2048:1152:flags=lanczos,"
              f"crop={W}:{H}:x='{x_motion}':y='{y_motion}',"
              f"fps=30,setsar=1,{grade},format=yuv420p")
        _run([ffmpeg_bin(),"-y","-loglevel","error","-stream_loop","-1",
              "-i",str(src),"-t",f"{dur:.3f}","-vf",vf,"-an",*ENC,str(out)])
    return out


def _append_credits_card(video_path, credits_path):
    """Append a restrained source-credit slate to the rendered documentary."""
    import textwrap

    credits_file = Path(credits_path)
    if not credits_file.is_file():
        return video_path
    raw_lines = [
        line.strip() for line in credits_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("DOCUMENTARY SOURCE CREDITS")
        and not line.strip().startswith("Human rights and context review")
    ]
    if not raw_lines:
        return video_path
    display_lines = []
    for line in raw_lines:
        display_lines.extend(textwrap.wrap(line, width=100) or [""])
    duration = max(10.0, 6.0 + len(display_lines) * 0.48)

    def ass_time(seconds):
        centiseconds = int(round(seconds * 100))
        return f"{centiseconds // 360000}:{(centiseconds // 6000) % 60:02d}:{(centiseconds % 6000) / 100:05.2f}"

    body = r"\N".join(line.replace("{", "(").replace("}", ")") for line in display_lines)
    ass_path = Path(video_path).parent / "source_credits.ass"
    ass_content = "\n".join([
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920",
        "PlayResY: 1080", "WrapStyle: 2", "", "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Heading,Noto Sans,48,&H00D6C39A,&H000000FF,&H0010151D,&H0010151D,1,0,0,0,100,100,0,0,1,0,0,8,110,110,70,1",
        "Style: Credit,Noto Sans,25,&H00F2F2F2,&H000000FF,&H0010151D,&H0010151D,0,0,0,0,100,100,0,0,1,0,0,7,120,120,170,1",
        "", "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        f"Dialogue: 0,0:00:00.00,{ass_time(duration)},Heading,,0,0,0,,SOURCE CREDITS",
        f"Dialogue: 0,0:00:00.00,{ass_time(duration)},Credit,,0,0,0,,{body}",
        "",
    ])
    ass_path.write_text(ass_content, encoding="utf-8")
    temp_out = Path(video_path).with_name(Path(video_path).stem + "_with_credits.mp4")
    args = [
        ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video_path),
        "-f", "lavfi", "-i", f"color=c=0x10151d:s=1920x1080:r=30:d={duration:.2f}",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration:.2f}",
        "-filter_complex",
        f"[1:v]{ffmpeg_utils.subtitles_filter(ass_path)},format=yuv420p,setpts=PTS-STARTPTS[cv];"
        "[0:v]setpts=PTS-STARTPTS[v0];[0:a]aresample=44100,asetpts=PTS-STARTPTS[a0];"
        "[2:a]asetpts=PTS-STARTPTS[ca];[v0][a0][cv][ca]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
        "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac",
        "-b:a", "256k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart",
        str(temp_out),
    ]
    try:
        _run(args)
        if temp_out.is_file() and temp_out.stat().st_size > 100_000:
            temp_out.replace(video_path)
    finally:
        temp_out.unlink(missing_ok=True)
        ass_path.unlink(missing_ok=True)  # scratch file; never leave it beside deliverables
    return video_path

def _segment_key(src, dur, is_image, shot):
    """Fingerprint of everything that determines a rendered segment."""
    try:
        stat = Path(src).stat()
        identity = [str(Path(src).resolve()), stat.st_size, int(stat.st_mtime)]
    except OSError:
        identity = [str(src), 0, 0]
    from core.visual_consistency import color_grade_filter
    payload = [identity, round(float(dur), 3), bool(is_image),
               (shot or {}).get("cinema_settings") or {}, color_grade_filter(), W, H, ENC]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _cached_seg(src, idx, dur, is_image, bd, shot=None, reuse=False):
    """Render one segment, reusing an intact identical one from an earlier attempt."""
    out = bd / f"lseg{idx}.mp4"
    key_path = bd / f"lseg{idx}.key"
    key = _segment_key(src, dur, is_image, shot)
    if reuse and out.is_file() and out.stat().st_size > 0 and key_path.is_file():
        try:
            if key_path.read_text(encoding="utf-8").strip() == key:
                return out
        except OSError:
            pass
    key_path.unlink(missing_ok=True)
    result = _seg(src, idx, dur, is_image, bd, shot)
    key_path.write_text(key, encoding="utf-8")
    return result


def render(sections, voice, ass, total, out, music=None, music_vol=0.18, credits_path=None,
           work_dir=None):
    """Render a documentary.

    Intermediate segments go to ``work_dir`` (a private temporary directory by
    default). When the caller supplies a persistent ``work_dir``, segments
    already rendered by an interrupted attempt are reused, so a crash near the
    end of a long render no longer repeats hours of encoding. The final MP4 is
    moved into place only after a complete, successful render.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    owns_work_dir = work_dir is None
    bd = Path(tempfile.mkdtemp(prefix=f".{out.stem}_work_", dir=str(out.parent))) if owns_work_dir \
        else Path(work_dir)
    bd.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(f".{out.stem}.partial{out.suffix or '.mp4'}")
    try:
        _render(sections, voice, ass, total, partial, bd, music, music_vol, credits_path,
                reuse=not owns_work_dir)
        if not partial.is_file() or partial.stat().st_size == 0:
            raise RuntimeError("FFmpeg finished without producing a documentary video.")
        os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)
        if owns_work_dir and os.getenv("KEEP_RENDER_WORK", "").strip().lower() not in {"1", "true", "yes"}:
            shutil.rmtree(bd, ignore_errors=True)
    return out


def _render(sections, voice, ass, total, out, bd, music, music_vol, credits_path, reuse=False):
    T = total + 1.5
    valid = []
    for i, sec in enumerate(sections):
        m = sec.get("_media",{}); src = Path(m.get("path","")) if m.get("path") else None
        if src and src.exists():
            d = sec.get("_dur",10.0)+FADE
            valid.append((i, _cached_seg(src, i, d, m.get("type")=="image", bd, sec, reuse=reuse),
                          sec.get("_dur",10.0), sec))
    if not valid: raise RuntimeError("No visual segments")
    transitions = [_transition_for(valid[k][3], valid[k+1][3], k) for k in range(len(valid)-1)]
    parts, joined_durations, transitions = reduce_segments(
        [item[1] for item in valid], [item[2] for item in valid], transitions, FADE, bd)
    filt = video_chain(joined_durations, transitions, FADE, "vjoin") \
        + f";[vjoin]{ffmpeg_utils.subtitles_filter(ass)}[vout];"
    inputs = []
    for p2 in parts: inputs += ["-i",str(p2)]
    inputs += ["-i",str(voice)]
    valid = parts
    n = len(valid); norm = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    afilt = [f"[{n}:a]{norm},apad=whole_dur={T:.3f}[va]"]
    mix_in = ["[va]"]; idx = n+1
    if music and Path(music).exists():
        inputs += ["-stream_loop","-1","-t",f"{T+1:.2f}","-i",str(music)]
        afilt[0] = f"[{n}:a]{norm},apad=whole_dur={T:.3f}[va0];[va0]asplit=2[va][key]"
        afilt.append(f"[{idx}:a]{norm},loudnorm=I=-14:TP=-2:LRA=7,aresample=44100,"
                     f"highpass=f=100,lowpass=f=10000,volume={music_vol},afade=t=in:st=0:d=1.5[mu]")
        afilt.append("[mu]asplit=2[mu1][mu2];[mu1][key]"
                     "sidechaincompress=threshold=0.03:ratio=5:attack=15:release=600:makeup=1[duck]")
        mix_in = ["[va]","[duck]"]
    fade = f"afade=t=out:st={max(T-2.0,0):.3f}:d=2.0"
    afilt.append("".join(mix_in)+f"amix=inputs={len(mix_in)}:duration=first:dropout_transition=0:"
                 f"normalize=0,alimiter=limit=0.95,{fade},{_audio_master_filter()}[aout]")
    filt += ";".join(afilt)
    _run([ffmpeg_bin(),"-y","-loglevel","error",*inputs,"-filter_complex",filt,
          "-map","[vout]","-map","[aout]","-t",f"{T:.3f}",*ENC,
          "-c:a","aac","-b:a","256k","-ar","44100","-ac","2","-movflags","+faststart",str(out)])
    if credits_path:
        _append_credits_card(out, credits_path)
    return out
