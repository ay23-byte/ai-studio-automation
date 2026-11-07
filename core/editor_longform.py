import subprocess
from pathlib import Path

W, H = 1920, 1080
FADE = 0.4
ENC = ["-c:v","libx264","-preset","slow","-crf","18","-profile:v","high",
       "-level","4.1","-pix_fmt","yuv420p","-r","30","-video_track_timescale","15360"]
TRANSITIONS = ["fade", "smoothleft", "fade", "circlecrop", "fade", "wipeleft"]
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


def _run(cmd): subprocess.run(cmd, check=True)


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


def _seg(src, idx, dur, is_image, bd):
    out = bd / f"lseg{idx}.mp4"
    if is_image:
        frames = max(int(dur*30),2)
        z = f"1+0.05*on/{frames}" if idx%2==0 else f"1.05-0.05*on/{frames}"
        pos = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        from core.visual_consistency import color_grade_filter
        grade = color_grade_filter()
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W*2}:{H*2},zoompan=z='{z}':{pos}:d=1:s={W}x{H}:fps=30,"
              f"{grade},format=yuv420p")
        _run(["ffmpeg","-y","-loglevel","error","-loop","1","-framerate","30",
              "-t",f"{dur:.3f}","-i",str(src),"-vf",vf,"-an",*ENC,str(out)])
    else:
        from core.visual_consistency import color_grade_filter
        grade = color_grade_filter()
        # Give moving B-roll a restrained, slow camera drift. The 6.7% overscan
        # prevents empty edges while the crop travels gently across the frame.
        x_motion, y_motion = _video_motion_expressions(idx)
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W}:{H},scale=2048:1152:flags=lanczos,"
              f"crop={W}:{H}:x='{x_motion}':y='{y_motion}',"
              f"fps=30,setsar=1,{grade},format=yuv420p")
        _run(["ffmpeg","-y","-loglevel","error","-stream_loop","-1",
              "-i",str(src),"-t",f"{dur:.3f}","-vf",vf,"-an",*ENC,str(out)])
    return out

def render(sections, voice, ass, total, out, music=None, music_vol=0.18):
    T = total + 1.5; bd = out.parent
    valid = []
    for i, sec in enumerate(sections):
        m = sec.get("_media",{}); src = Path(m.get("path","")) if m.get("path") else None
        if src and src.exists():
            d = sec.get("_dur",10.0)+FADE
            valid.append((i, _seg(src, i, d, m.get("type")=="image", bd), sec.get("_dur",10.0), sec))
    if not valid: raise RuntimeError("No visual segments")
    if len(valid)==1:
        _, p, _, _shot = valid[0]
        filt = f"subtitles={ass}[vout];"; inputs=["-i",str(p),"-i",str(voice)]
    else:
        offsets, acc = [], 0.0
        for j in range(len(valid)-1):
            acc += valid[j][2]; offsets.append(acc)
        chain, prev = [], "[0:v]"
        for k, (off, (_, p2, _, current_shot)) in enumerate(zip(offsets, valid[1:])):
            lab = f"[x{k}]"
            previous_shot = valid[k][3]
            tr = _transition_for(previous_shot, current_shot, k)
            chain.append(f"{prev}[{k+1}:v]xfade=transition={tr}:duration={FADE}:offset={off:.3f}{lab}")
            prev = lab
        filt = ";".join(chain+[f"{prev}subtitles={ass}[vout]"])+";"
        inputs = []
        for _, p2, _, _shot in valid: inputs += ["-i",str(p2)]
        inputs += ["-i",str(voice)]
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
    _run(["ffmpeg","-y","-loglevel","error",*inputs,"-filter_complex",filt,
          "-map","[vout]","-map","[aout]","-t",f"{T:.3f}",*ENC,
          "-c:a","aac","-b:a","256k","-ar","44100","-ac","2","-movflags","+faststart",str(out)])
    return out
