import asyncio, subprocess, re
from pathlib import Path
import edge_tts
from core.config import VOICE_HINDI, VOICE_ENGLISH, VOICE_FEMALE

def pick_voice(language="hindi", gender="male"):
    if language == "english": return VOICE_ENGLISH
    if gender == "female": return VOICE_FEMALE
    return VOICE_HINDI

def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[।.!?])\s+", text.strip()) if s.strip()]

def _prosody(i, n, s):
    rate, pitch = "+3%", "+0Hz"
    if i == 0: rate, pitch = "-2%", "-2Hz"
    if s.rstrip().endswith("?"): pitch = "+3Hz"
    if i == n-1: rate = "+0%"
    return rate, pitch

async def _synth(text, voice, rate, pitch, path):
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path))

def _to_wav(mp3, wav):
    subprocess.run(["ffmpeg","-y","-loglevel","error","-i",str(mp3),
                    "-ar","44100","-ac","1","-c:a","pcm_s16le",str(wav)], check=True)

def _dur(p):
    return float(subprocess.check_output(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(p)]).strip())

def _join(wavs, durs, pauses, out):
    n = len(wavs); inputs, filt = [], []
    for i, w in enumerate(wavs):
        inputs += ["-i", str(w)]
        pad = f",apad=pad_dur={pauses[i]:.3f}" if pauses[i] > 0 else ""
        filt.append(f"[{i}:a]aresample=44100{pad}[a{i}]")
    filt.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat]")
    filt.append("[cat]highpass=f=70,acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2,"
                "loudnorm=I=-16:TP=-1.5:LRA=11[vo]")
    subprocess.run(["ffmpeg","-y","-loglevel","error",*inputs,"-filter_complex",";".join(filt),
                    "-map","[vo]","-ar","44100","-ac","1","-c:a","pcm_s16le",str(out)], check=True)

def synthesise(text, build_dir, language="hindi", gender="male"):
    voice = pick_voice(language, gender)
    sents = _sentences(text); n = len(sents)
    wavs, durs = [], []
    for i, s in enumerate(sents):
        mp3 = build_dir / f"s{i}.mp3"; wav = build_dir / f"s{i}.wav"
        rate, pitch = _prosody(i, n, s)
        asyncio.run(_synth(s, voice, rate, pitch, mp3))
        _to_wav(mp3, wav); wavs.append(wav); durs.append(_dur(wav))
    pauses = [0.45 if i==0 else (0.5 if sents[i].rstrip().endswith("?") else 0.28)
              for i in range(n-1)] + [0.0]
    out = build_dir / "voice.wav"
    _join(wavs, durs, pauses, out)
    timings, t = [], 0.0
    for d, p, s in zip(durs, pauses, sents):
        timings.append((t, t+d, s)); t += d+p
    return out, timings
