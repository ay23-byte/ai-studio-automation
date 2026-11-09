import asyncio
import inspect
import json
import os
import re
import subprocess
from pathlib import Path

import edge_tts
from core.config import VOICE_HINDI, VOICE_ENGLISH, VOICE_FEMALE, VOICE_ENGLISH_FEMALE

_CHATTERBOX_MODEL = None

def pick_voice(language="hindi", gender="male"):
    # Explicit voice selection makes auditions reproducible.
    override = os.getenv("TTS_VOICE", "").strip()
    if override:
        return override
    if language == "english":
        return VOICE_ENGLISH_FEMALE if gender == "female" else VOICE_ENGLISH
    if gender == "female":
        return VOICE_FEMALE
    return VOICE_HINDI

def _sentences(text):
    """Split narration at sentence boundaries, including Hindi danda punctuation."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return [s.strip() for s in re.split(r"(?<=[।!?])\s+|(?<=[.!?])\s+", clean) if s.strip()]


def _speech_chunks(text, max_chars=220):
    """Group short sentences into natural TTS phrases without splitting sentences."""
    sentences = _sentences(text)
    chunks, current = [], ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _apply_pronunciation_map(text, mapping=None):
    """Apply optional user-defined word/phrase pronunciation substitutions."""
    if mapping is None:
        try:
            mapping = json.loads(os.getenv("TTS_PRONUNCIATION_MAP", "{}"))
        except (TypeError, ValueError):
            print("TTS_PRONUNCIATION_MAP is invalid JSON; ignoring pronunciation overrides.")
            mapping = {}
    if not isinstance(mapping, dict):
        return str(text or "")
    result = str(text or "")
    for source, spoken in mapping.items():
        source, spoken = str(source).strip(), str(spoken)
        if source:
            pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(source) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
            result = pattern.sub(lambda _match: spoken, result)
    return result


def _pause_after(chunk):
    """Natural pause after a phrase, based on its ending punctuation."""
    ending = (chunk or "").rstrip()[-1:] 
    if ending in ("?", "؟", "!"):
        return 0.78
    if ending in (".", "।", "…"):
        return 0.62
    if ending in (",", ";", ":", "،"):
        return 0.34
    return 0.24


def _prosody(i, n, sentence):
    # Keep one voice across the video; make openings and sentence endings clearer.
    rate, pitch = os.getenv("TTS_RATE", "-7%"), os.getenv("TTS_PITCH", "+0Hz")
    if i == 0:
        rate = "-8%"
    if sentence.rstrip().endswith(("?", "؟")):
        pitch = "+2Hz"
    if i == n - 1:
        rate = "-6%"
    return rate, pitch


async def _synth_edge(text, voice, rate, pitch, path):
    # Edge TTS occasionally returns NoAudioReceived during transient service/network
    # failures. Retry before giving up so one temporary response does not kill a build.
    last_error = None
    for attempt in range(3):
        try:
            await edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch
            ).save(str(path))
            if path.exists() and path.stat().st_size > 0:
                return
            raise RuntimeError("Edge TTS returned an empty audio file.")
        except Exception as exc:
            last_error = exc
            path.unlink(missing_ok=True)
            if attempt < 2:
                await asyncio.sleep(2 ** (attempt + 1))
    raise RuntimeError(
        f"Edge TTS failed after 3 attempts for voice {voice}: {last_error}"
    ) from last_error

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
        # PyPI releases of chatterbox-tts may not expose the newer t3_model
        # parameter. Pass it only when the installed version supports it.
        loader = ChatterboxMultilingualTTS.from_pretrained
        loader_params = inspect.signature(loader).parameters
        if "t3_model" in loader_params:
            _CHATTERBOX_MODEL = loader(device="cpu", t3_model="v3")
        else:
            print(
                "Installed Chatterbox version does not support t3_model; "
                "loading its default multilingual checkpoint."
            )
            _CHATTERBOX_MODEL = loader(device="cpu")
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

def _edge_voice_candidates(language, gender):
    """Try the configured voice first, then independent Edge voices."""
    preferred = pick_voice(language, gender)
    if language == "english":
        fallbacks = ["en-US-AriaNeural", "en-GB-SoniaNeural", "en-IN-PrabhatNeural"]
    elif gender == "female":
        fallbacks = ["hi-IN-SwaraNeural", "hi-IN-AnanyaNeural", "en-IN-NeerjaNeural"]
    else:
        fallbacks = ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural", "en-IN-PrabhatNeural"]
    return list(dict.fromkeys([preferred, *fallbacks]))


def _synth_all_edge(sentences, language, gender, build_dir):
    """Synthesize the complete narration with one voice; never mix voices mid-video."""
    last_error = None
    for voice in _edge_voice_candidates(language, gender):
        wavs = []
        try:
            for i, sentence in enumerate(sentences):
                mp3 = build_dir / f"s{i}.mp3"
                wav = build_dir / f"s{i}.wav"
                mp3.unlink(missing_ok=True)
                wav.unlink(missing_ok=True)
                rate, pitch = _prosody(i, len(sentences), sentence)
                asyncio.run(_synth_edge(sentence, voice, rate, pitch, mp3))
                _to_wav(mp3, wav)
                if not wav.exists() or wav.stat().st_size == 0:
                    raise RuntimeError(f"Edge TTS produced an empty WAV for chunk {i + 1}.")
                wavs.append(wav)
            print(f"Edge TTS narration completed with voice {voice}.")
            return wavs
        except Exception as exc:
            last_error = exc
            print(f"WARNING: Edge voice {voice} failed; trying another voice: {exc}")
            for i in range(len(sentences)):
                (build_dir / f"s{i}.mp3").unlink(missing_ok=True)
                (build_dir / f"s{i}.wav").unlink(missing_ok=True)
    raise RuntimeError(f"All Edge TTS voices failed: {last_error}") from last_error


def _synth_all_espeak(sentences, language, build_dir):
    """Offline last-resort narration; requires espeak-ng installed by CI."""
    voice = "en" if language == "english" else "hi"
    wavs = []
    for i, sentence in enumerate(sentences):
        wav = build_dir / f"s{i}.wav"
        wav.unlink(missing_ok=True)
        subprocess.run(
            ["espeak-ng", "-v", voice, "-s", "148", "-a", "175", "-w", str(wav), sentence],
            check=True, capture_output=True, text=True,
        )
        if not wav.exists() or wav.stat().st_size < 128:
            raise RuntimeError(f"espeak-ng produced empty audio for chunk {i + 1}.")
        wavs.append(wav)
    print(f"Offline espeak-ng fallback completed using voice {voice}.")
    return wavs

def synthesise(text, build_dir, language="hindi", gender="male"):
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    sentences = _speech_chunks(_apply_pronunciation_map(text))
    if not sentences:
        raise ValueError("No narration text was provided.")
    # Pause after the phrase that was just spoken (not the next phrase).
    pauses = [_pause_after(chunk) for chunk in sentences[:-1]] + [0.0]

    requested_engine = os.getenv("TTS_ENGINE", "edge").strip().lower()
    engine = requested_engine
    fallback_reason = None
    is_ci = os.getenv("CI", "").strip().lower() == "true" or os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"
    allow_chatterbox_ci = os.getenv("ALLOW_CHATTERBOX_ON_CI", "").strip().lower() == "true"
    if engine == "chatterbox" and is_ci and not allow_chatterbox_ci:
        fallback_reason = "Chatterbox CI inference was not explicitly enabled; trying Edge TTS first."
        print("WARNING: " + fallback_reason)
        engine = "edge"

    # Provider cascade: requested engine -> multiple Edge voices -> offline espeak-ng.
    # A transient network outage must not abort a documentary after script generation.
    if engine == "chatterbox":
        try:
            wavs = _synth_all_chatterbox(sentences, language, build_dir)
        except Exception as exc:
            fallback_reason = f"Chatterbox failed ({type(exc).__name__}: {exc}); trying Edge TTS."
            print("WARNING: " + fallback_reason)
            for i in range(len(sentences)):
                (build_dir / f"s{i}.wav").unlink(missing_ok=True)
            try:
                wavs = _synth_all_edge(sentences, language, gender, build_dir)
                engine = "edge"
            except Exception as edge_exc:
                fallback_reason += f" Edge TTS also failed ({type(edge_exc).__name__}: {edge_exc}); using offline espeak-ng."
                print("WARNING: " + fallback_reason)
                wavs = _synth_all_espeak(sentences, language, build_dir)
                engine = "espeak-ng"
    else:
        try:
            wavs = _synth_all_edge(sentences, language, gender, build_dir)
            engine = "edge"
        except Exception as edge_exc:
            fallback_reason = f"All Edge TTS voices failed ({type(edge_exc).__name__}: {edge_exc}); using offline espeak-ng."
            print("WARNING: " + fallback_reason)
            wavs = _synth_all_espeak(sentences, language, build_dir)
            engine = "espeak-ng"

    durations = [_dur(wav) for wav in wavs]
    out = build_dir / "voice.wav"
    _join(wavs, pauses, out)
    timings, current = [], 0.0
    for duration, pause, sentence in zip(durations, pauses, sentences):
        timings.append((current, current + duration, sentence))
        current += duration + pause
    report = {
        "requested_engine": requested_engine,
        "actual_engine": engine,
        "fallback_reason": fallback_reason,
        "voice": pick_voice(language, gender) if engine == "edge" else ("Chatterbox multilingual" if engine == "chatterbox" else "espeak-ng offline fallback"),
        "language": language,
        "gender": gender,
        "chunk_count": len(sentences),
        "speech_seconds": round(sum(durations), 3),
        "inserted_pause_seconds": round(sum(pauses), 3),
        "assembled_duration_seconds": round(_dur(out), 3),
        "human_voice_review": "pending",
        "notice": "Technical metadata does not certify naturalness, emotion, pronunciation or professional performance; listen to the complete narration.",
    }
    (build_dir / "narration_synthesis_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out, timings


from core.monitoring import monitored as _monitored
_synthesise_raw = synthesise
synthesise = _monitored("tts", "synthesise", _synthesise_raw)
