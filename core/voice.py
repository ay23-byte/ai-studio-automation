import asyncio
import os
import re
import subprocess
from pathlib import Path

import edge_tts
from core.config import VOICE_HINDI, VOICE_ENGLISH, VOICE_FEMALE

_CHATTERBOX_MODEL = None

def pick_voice(language="hindi", gender="male"):
    if language == "english":
        return VOICE_ENGLISH
    if gender == "female":
        return VOICE_FEMALE
    return VOICE_HINDI

def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[।.!?])\\s+", text.strip()) if s.strip()]

def _prosody(i, n, sentence):
    rate, pitch = "+3%", "+0Hz"
    if i == 0:
        rate, pitch = "-2%", "-2Hz"
    if sentence.rstrip().endswith("?"):
        pitch = "+3Hz"
    if i == n - 1:
        rate = "+0%"
    return rate, pitch

async def _synth_edge(text, voice, rate, pitch, path):
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path))

def _to_wav(mp3, wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)

def _dur(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)]).strip())

def _join(wavs, pauses, out):
    inputs, filters = [], []
    for i, wav in enumerate(wavs):
        inputs += ["-i", str(wav)]
        pad = f",apad=pad_dur={pauses[i]:.3f}" if pauses[i] > 0 else ""
        filters.append(f"[{i}:a]aresample=44100{pad}[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(len(wavs))) +
                   f"concat=n={len(wavs)}:v=0:a=1[cat]")
    filters.append("[cat]highpass=f=70,acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2,loudnorm=I=-16:TP=-1.5:LRA=11[vo]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filters), "-map", "[vo]",
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out)], check=True)

def _get_chatterbox():
    global _CHATTERBOX_MODEL
    if _CHATTERBOX_MODEL is None:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        torch.set_num_threads(max(1, int(os.getenv("CHATTERBOX_TORCH_THREADS", "2"))))
        _CHATTERBOX_MODEL = ChatterboxMultilingualTTS.from_pretrained(
            device="cpu", t3_model="v3"
        )
    return _CHATTERBOX_MODEL

def _synth_chatterbox(text, language, path):
    import torchaudio
    model = _get_chatterbox()
    language_id = "en" if language == "english" else "hi"
    audio = model.generate(text, language_id=language_id)
    torchaudio.save(str(path), audio.detach().cpu(), model.sr)

def _synth_all_chatterbox(sentences, language, build_dir):
    wavs = []
    for i, sentence in enumerate(sentences):
        wav = build_dir / f"s{i}.wav"
        _synth_chatterbox(sentence, language, wav)
        wavs.append(wav)
    return wavs

def _synth_all_edge(sentences, language, gender, build_dir):
    voice = pick_voice(language, gender)
    wavs = []
    for i, sentence in enumerate(sentences):
        mp3 = build_dir / f"s{i}.mp3"
        wav = build_dir / f"s{i}.wav"
        rate, pitch = _prosody(i, len(sentences), sentence)
        asyncio.run(_synth_edge(sentence, voice, rate, pitch, mp3))
        _to_wav(mp3, wav)
        wavs.append(wav)
    return wavs

def synthesise(text, build_dir, language="hindi", gender="male"):
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    sentences = _sentences(text)
    if not sentences:
        raise ValueError("No narration text was provided.")
    pauses = [0.45 if i == 0 else
              (0.5 if sentences[i].rstrip().endswith("?") else 0.28)
              for i in range(len(sentences) - 1)] + [0.0]

    engine = os.getenv("TTS_ENGINE", "edge").strip().lower()
    if engine == "chatterbox":
        try:
            wavs = _synth_all_chatterbox(sentences, language, build_dir)
        except Exception as exc:
            print(f"Chatterbox failed; falling back to Edge TTS: {exc}")
            for i in range(len(sentences)):
                (build_dir / f"s{i}.wav").unlink(missing_ok=True)
            wavs = _synth_all_edge(sentences, language, gender, build_dir)
    else:
        wavs = _synth_all_edge(sentences, language, gender, build_dir)

    durations = [_dur(wav) for wav in wavs]
    out = build_dir / "voice.wav"
    _join(wavs, pauses, out)
    timings, current = [], 0.0
    for duration, pause, sentence in zip(durations, pauses, sentences):
        timings.append((current, current + duration, sentence))
        current += duration + pause
    return out, timings
