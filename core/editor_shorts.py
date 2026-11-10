import subprocess, random
from pathlib import Path

W, H = 1080, 1920
FADE = 0.3
ENC = ["-c:v","libx264","-preset","fast","-crf","19","-profile:v","high",
       "-level","4.0","-pix_fmt","yuv420p","-r","30","-video_track_timescale","15360"]
TRANSITIONS = ["fade","smoothleft","fade","smoothright"]

def _run(cmd): subprocess.run(cmd, check=True)

def _img_size(p):
    out = subprocess.check_output(["ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","stream=width,height","-of","csv=p=0",str(p)],
        stderr=subprocess.DEVNULL).decode().strip().split(",")
    return int(out[0]), int(out[1])

def _whoosh(path):
    _run(["ffmpeg","-y","-loglevel","error","-f","lavfi",
          "-i","anoisesrc=d=0.7:c=pink:r=44100:a=0.7",
          "-af","highpass=f=250,lowpass=f=3500,afade=t=in:st=0:d=0.3,afade=t=out:st=0.3:d=0.4",
          "-ac","2",str(path)])

def _img_seg(src, idx, dur, bd, settings=None):
    out = bd / f"seg{idx}.mp4"
    w, h = _img_size(src)
    frames = max(int(dur*30), 2)
    from core.cinematic_motion import still_motion_parameters
    z, x, y = still_motion_parameters(settings, idx, frames)
    pos = f"x='{x}':y='{y}'"
    base = ["ffmpeg","-y","-loglevel","error","-loop","1","-framerate","30","-t",f"{dur:.3f}","-i",str(src)]
    if h/max(w,1) >= 1.5:
        vf = (f"scale=2160:3840:force_original_aspect_ratio=increase:flags=lanczos,crop=2160:3840,"
              f"zoompan=z='{z}':{pos}:d=1:s={W}x{H}:fps=30,"
              "eq=brightness=-0.03:saturation=1.05,vignette=PI/7,format=yuv420p")
        _run(base + ["-vf",vf,"-an",*ENC,str(out)])
    else:
        fh = min(int(round(W*h/max(w,1)/2))*2, H)
        fc = (f"[0:v]split=2[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"gblur=sigma=40,eq=brightness=-0.22:saturation=0.9[bg];"
              f"[b]scale={W*2}:-2:flags=lanczos,zoompan=z='{z}':{pos}:d=1:s={W}x{fh}:fps=30[fg];"
              f"[bg][fg]overlay=0:(H-h)/2,vignette=PI/7,format=yuv420p[v]")
        _run(base + ["-filter_complex",fc,"-map","[v]","-an",*ENC,str(out)])
    return out

def _clip_seg(src, idx, dur, bd, settings=None):
    out = bd / f"seg{idx}.mp4"
    sw = int(W*1.12)//2*2; sh = int(H*1.12)//2*2
    from core.cinematic_motion import video_crop_expressions
    x, y = video_crop_expressions(settings, idx, dur, sw-W, sh-H)
    vf = (f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos,crop={sw}:{sh},fps=30,"
          f"crop={W}:{H}:x='{x}':y='{y}',setsar=1,"
          "eq=brightness=-0.08:saturation=0.85,vignette=PI/6,format=yuv420p")
    _run(["ffmpeg","-y","-loglevel","error","-stream_loop","-1",
          "-i",str(src),"-t",f"{dur:.3f}","-vf",vf,"-an",*ENC,str(out)])
    return out

def render(media, lens, cuts, voice, ass, total, out, music=None, music_vol=0.45, sfx_vol=0.15):
    T = total + 0.6
    bd = out.parent
    parts = []
    for i, m in enumerate(media):
        d = lens[i] + FADE
        parts.append(_img_seg(Path(m["path"]), i, d, bd, m.get("cinema_settings")) if m["type"]=="image"
                     else _clip_seg(Path(m["path"]), i, d, bd, m.get("cinema_settings")))
    chain, prev, acc = [], "[0:v]", 0.0
    for k in range(1, len(parts)):
        acc += lens[k-1]; lab = f"[x{k}]"
        tr = TRANSITIONS[(k-1)%len(TRANSITIONS)]
        chain.append(f"{prev}[{k}:v]xfade=transition={tr}:duration={FADE}:offset={acc:.3f}{lab}")
        prev = lab
    filt = ";".join(chain + [f"{prev}subtitles={ass}[vout]"]) + ";"
    inputs = []
    for p in parts: inputs += ["-i",str(p)]
    inputs += ["-i",str(voice)]
    n = len(parts); idx = n+1
    norm = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
    afilt = [f"[{n}:a]{norm},apad=whole_dur={T:.3f}[va0]"]
    mix_in = []
    if music and Path(music).exists():
        inputs += ["-stream_loop","-1","-t",f"{T+1:.2f}","-i",str(music)]
        afilt.append("[va0]asplit=2[va][key]")
        afilt.append(f"[{idx}:a]{norm},loudnorm=I=-14:TP=-2:LRA=7,aresample=44100,"
                     f"highpass=f=100,lowpass=f=10000,volume={music_vol},afade=t=in:st=0:d=1.0[mu]")
        afilt.append("[mu]asplit=2[mu1][mu2];[mu1][key]"
                     "sidechaincompress=threshold=0.03:ratio=6:attack=12:release=450:makeup=1[duck]")
        mix_in = ["[va]","[duck]"]; idx += 1
    else:
        afilt[0] = f"[{n}:a]{norm},apad=whole_dur={T:.3f}[va]"; mix_in = ["[va]"]
    if sfx_vol > 0 and cuts:
        whoosh = bd/"whoosh.wav"; _whoosh(whoosh); inputs += ["-i",str(whoosh)]
        K = len(cuts)
        afilt.append(f"[{idx}:a]{norm},asplit={K}"+"".join(f"[w{i}]" for i in range(K)) if K>1
                     else f"[{idx}:a]{norm}[w0]")
        for i,tt in enumerate(cuts):
            ms = int(tt*1000)
            afilt.append(f"[w{i}]adelay={ms}|{ms},volume={sfx_vol}[d{i}]"); mix_in.append(f"[d{i}]")
    fade = f"afade=t=out:st={max(T-1.2,0):.3f}:d=1.2"
    afilt.append("".join(mix_in)+f"amix=inputs={len(mix_in)}:duration=first:dropout_transition=0:"
                 f"normalize=0,alimiter=limit=0.95,{fade}[aout]")
    filt += ";".join(afilt)
    _run(["ffmpeg","-y","-loglevel","error",*inputs,"-filter_complex",filt,
          "-map","[vout]","-map","[aout]","-t",f"{T:.3f}",*ENC,
          "-c:a","aac","-b:a","192k","-ar","48000","-ac","2","-movflags","+faststart",str(out)])
    return out
