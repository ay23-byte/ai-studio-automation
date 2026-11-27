import asyncio
import hashlib
import inspect
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from core.config import VOICE_HINDI, VOICE_ENGLISH, VOICE_FEMALE, VOICE_ENGLISH_FEMALE
from core.ffmpeg_utils import ffmpeg_bin, ffprobe_bin  # honours FFMPEG_BINARY / FFPROBE_BINARY

# Edge TTS is an optional provider.  It used to be imported here at module level,
# so every caller that only needed voice *selection* (and every test module that
# imported this file) crashed with ModuleNotFoundError in lightweight workflows
# that do not install it.  The package is now loaded when speech is synthesised.
try:  # pragma: no cover - exercised through _edge_module()
    import edge_tts
except ImportError:  # the provider is simply not installed
    edge_tts = None


class VoiceProviderUnavailable(RuntimeError):
    """A narration provider cannot be used at all (missing package or binary).

    Unlike a transient service failure there is no point retrying it, so the
    provider cascade moves straight to the next provider.
    """


def _edge_module():
    """Return the edge-tts module, or raise a clear, actionable error."""
    if edge_tts is None:
        raise VoiceProviderUnavailable(
            "Edge TTS is not installed in this environment. Install it with "
            "'pip install edge-tts' (it is included in requirements.txt and "
            "requirements-ci.txt), or set TTS_ENGINE to another provider.")
    return edge_tts


def provider_status() -> dict:
    """Which narration providers can run here; used by diagnostics, never raises."""
    import importlib.util
    import shutil
    try:
        chatterbox = importlib.util.find_spec("chatterbox") is not None
    except (ImportError, ValueError):
        chatterbox = False
    return {
        "edge": edge_tts is not None,
        "nvidia_magpie": bool(os.getenv("NVIDIA_API_KEY", "").strip()),
        "chatterbox": chatterbox,
        "espeak-ng": shutil.which("espeak-ng") is not None,
    }

# Voice that actually produced the last Edge narration (a fallback voice may differ
# from the configured one); written to narration_synthesis_report.json.
_LAST_EDGE_VOICE = None
_LAST_EDGE_FAILURES = []     # one line per Edge voice that gave up, for the synthesis report

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
    # A piece with no letter or digit ("...", a stray dash or quote) has nothing to say. Edge TTS
    # answers such text with "No audio was received", which no retry can cure.
    return [s.strip() for s in re.split(r"(?<=[।!?])\s+|(?<=[.!?])\s+", clean) if _speakable(s)]


def _speakable(text):
    """True when the text holds at least one letter or digit."""
    import unicodedata
    return any(unicodedata.category(char)[0] in "LN" for char in str(text or ""))


_PLAIN_PUNCTUATION = set(".,?!;:'-%\u0964")


def _plain_speech_text(text):
    """The same words with every symbol a speech service may refuse removed.

    Letters, marks and digits of any script are kept, with spaces and basic punctuation.
    """
    import unicodedata
    kept = []
    for char in str(text or ""):
        kind = unicodedata.category(char)[0]
        if kind in "LMN" or char in _PLAIN_PUNCTUATION:
            kept.append(char)
        elif kind in "ZPS" or char.isspace():
            kept.append(" ")
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def _short_error(exc, limit=50):
    """The root cause of a provider error as ``Type: message``, short enough for a notice."""
    cause = exc.__cause__ or exc
    return f"{type(cause).__name__}: {' '.join(str(cause).split())[:limit]}"


def _speech_chunks(text, max_chars=220, language=None):
    """Group sentences without mixing language IDs inside a Chatterbox Hinglish chunk.

    A mixed Hindi-English sentence stays intact and uses the Hindi multilingual
    token; an English-only sentence gets the English token. Aggregating adjacent
    Hindi and English sentences can otherwise select the wrong pronunciation
    for the entire phrase.
    """
    sentences = _sentences(text)
    chunks, current = [], ""
    current_language = None
    hinglish = str(language or "").strip().lower() == "hinglish"
    for sentence in sentences:
        sentence_language = _chatterbox_language_id(sentence, "hinglish") if hinglish else None
        candidate = f"{current} {sentence}".strip()
        language_changed = bool(current and hinglish and sentence_language != current_language)
        if current and (len(candidate) > max_chars or language_changed):
            chunks.append(current)
            current = sentence
            current_language = sentence_language
        else:
            current = candidate
            if current_language is None:
                current_language = sentence_language
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
    """Natural pause after a phrase; optionally tighten pauses for fast news delivery."""
    ending = (chunk or "").rstrip()[-1:]
    if ending in ("?", "؟"):
        base = 0.30
    elif ending == "!":
        base = 0.28
    elif ending in (".", "।", "…"):
        base = 0.26
    elif ending in (",", ";", ":", "،"):
        base = 0.18
    else:
        base = 0.12
    try:
        scale = max(0.25, min(float(os.getenv("TTS_PAUSE_SCALE", "1.0") or 1.0), 1.5))
    except ValueError:
        scale = 1.0
    return round(base * scale, 3)

def _prosody(i, n, sentence):
    # Keep one voice across the video; make openings and sentence endings clearer.
    # A restrained +3% delivery feels more like a confident documentary
    # presenter without rushing names, dates or technical explanations.
    rate, pitch = os.getenv("TTS_RATE", "+3%"), os.getenv("TTS_PITCH", "+0Hz")
    if i == 0:
        rate = os.getenv("TTS_OPENING_RATE", "+2%")
    if sentence.rstrip().endswith(("?", "؟")):
        pitch = "+1Hz"
    if i == n - 1:
        rate = os.getenv("TTS_CLOSING_RATE", "+3%")
    return rate, pitch


async def _synth_edge(text, voice, rate, pitch, path):
    # Edge TTS occasionally returns NoAudioReceived during transient service/network
    # failures. Retry before giving up so one temporary response does not kill a build.
    last_error = None
    provider = _edge_module()  # a missing package is not retried
    for attempt in range(3):
        try:
            # The service streams over a websocket with no deadline of its own; a
            # stalled connection would otherwise hold the build until the job is killed.
            await asyncio.wait_for(
                provider.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path)),
                timeout=float(os.getenv("EDGE_TTS_TIMEOUT_SECONDS", "90") or 90))
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

def trim_edge_silence(wav, threshold_db=-48.0, keep_seconds=0.03):
    """Remove silence from the two ENDS of a 16-bit PCM WAV; pauses inside are never touched.

    Edge TTS appends about a second of digital silence to every phrase; joined over 100+
    phrases that is minutes of dead air. The previous FFmpeg ``silenceremove`` filter with
    ``stop_periods=1`` did not trim the end: it stopped at the FIRST pause of 0.12 s, so every
    phrase lost everything after its first comma or full stop. Trimming is done here on the
    samples so the result does not depend on the FFmpeg version.

    Returns {"source_seconds", "kept_seconds", "removed_start_seconds", "removed_end_seconds"}.
    """
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as handle:
        params = handle.getparams()
        raw = handle.readframes(params.nframes)
    if params.sampwidth != 2:
        raise ValueError(f"Narration WAV must be 16-bit PCM, got {params.sampwidth * 8}-bit.")
    samples = np.frombuffer(raw, dtype="<i2").reshape(-1, params.nchannels)
    total = len(samples)
    source_seconds = total / float(params.framerate or 1)
    if not total:
        return {"source_seconds": 0.0, "kept_seconds": 0.0, "removed_start_seconds": 0.0, "removed_end_seconds": 0.0}
    limit = 32768.0 * (10.0 ** (float(threshold_db) / 20.0))
    loud = np.flatnonzero(np.abs(samples.astype(np.int32)).max(axis=1) > limit)
    if not len(loud):                     # nothing audible: leave the file for the caller to reject
        return {"source_seconds": round(source_seconds, 3), "kept_seconds": round(source_seconds, 3),
                "removed_start_seconds": 0.0, "removed_end_seconds": 0.0}
    margin = int(float(keep_seconds) * params.framerate)
    first = max(0, int(loud[0]) - margin)
    last = min(total, int(loud[-1]) + 1 + margin)
    if first > 0 or last < total:
        with wave.open(str(wav), "wb") as handle:
            handle.setparams(params)
            handle.writeframes(samples[first:last].tobytes())
    return {"source_seconds": round(source_seconds, 3),
            "kept_seconds": round((last - first) / float(params.framerate), 3),
            "removed_start_seconds": round(first / float(params.framerate), 3),
            "removed_end_seconds": round((total - last) / float(params.framerate), 3)}


def _to_wav(mp3, wav):
    """Decode one synthesised phrase to mono 44.1 kHz PCM and trim the silence at its two ends."""
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(mp3),
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
                    str(wav)], check=True, timeout=render_timeout())
    return trim_edge_silence(wav)


class NarrationIncompleteError(RuntimeError):
    """The synthesised audio is too short to contain the words it was given."""


def _spoken_characters(text) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def check_narration_complete(sentences, durations):
    """Refuse narration whose audio cannot contain its text.

    Natural speech runs at roughly 12-20 characters a second (150-200 words a minute).
    Audio far shorter than that for its text means words were dropped: a truncated provider
    response, or a trim that cut into the speech. Limits: ``NARRATION_MAX_CHARS_PER_SECOND``
    (26) over the whole narration, and one and a half times that for any single phrase of
    60 characters or more.

    Returns {"characters", "words", "speech_seconds", "characters_per_second", "words_per_minute"}.
    """
    try:
        limit = float(os.getenv("NARRATION_MAX_CHARS_PER_SECOND", "26") or 26)
    except ValueError:
        limit = 26.0
    limit = max(10.0, limit)
    characters = sum(_spoken_characters(sentence) for sentence in sentences)
    words = sum(len(str(sentence).split()) for sentence in sentences)
    seconds = float(sum(durations))
    rate = characters / seconds if seconds > 0 else float("inf")
    if characters and rate > limit:
        raise NarrationIncompleteError(
            f"Narration audio is too short for its text: {characters} characters ({words} words) in "
            f"{seconds:.1f} s is {rate:.0f} characters a second; speech runs at 12-20. "
            "Part of the narration was not spoken. Refusing to build a documentary with missing narration.")
    for index, (sentence, duration) in enumerate(zip(sentences, durations), start=1):
        size = _spoken_characters(sentence)
        if size >= 60 and (duration <= 0 or size / duration > limit * 1.5):
            raise NarrationIncompleteError(
                f"Narration phrase {index} of {len(sentences)} is too short for its text: {size} characters "
                f"in {duration:.2f} s. Part of the phrase was not spoken: {str(sentence)[:90]!r}")
    return {"characters": characters, "words": words, "speech_seconds": round(seconds, 3),
            "characters_per_second": round(rate, 2) if seconds > 0 else None,
            "words_per_minute": round(words / seconds * 60.0, 1) if seconds > 0 else None}


def _dur(path):
    return float(subprocess.check_output(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], timeout=PROBE_TIMEOUT).strip())

def _join(wavs, pauses, out, speed=1.0):
    inputs, filters = [], []
    for i, wav in enumerate(wavs):
        inputs += ["-i", str(wav)]
        pad = f",apad=pad_dur={pauses[i]:.3f}" if pauses[i] > 0 else ""
        filters.append(f"[{i}:a]aresample=44100,atempo={max(0.5, min(float(speed), 2.0)):.3f}{pad}[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(len(wavs))) +
                   f"concat=n={len(wavs)}:v=0:a=1[cat]")
    # Keep documentary speech present without crushing its natural dynamics.
    try:
        ratio = max(1.0, min(float(os.getenv("VOICE_COMPRESSOR_RATIO", "2.2")), 5.0))
        makeup = max(0.0, min(float(os.getenv("VOICE_COMPRESSOR_MAKEUP_DB", "1.0")), 6.0))
    except ValueError:
        ratio, makeup = 2.2, 1.0
    filters.append(f"[cat]highpass=f=70,acompressor=threshold=-20dB:ratio={ratio:.2f}:attack=8:release=100:makeup={makeup:.2f},loudnorm=I=-16:TP=-1.5:LRA=11[vo]")
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", ";".join(filters), "-map", "[vo]",
                    "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out)], check=True, timeout=render_timeout())

def _get_chatterbox():
    global _CHATTERBOX_MODEL
    if _CHATTERBOX_MODEL is None:
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        torch.set_num_threads(max(1, int(os.getenv("CHATTERBOX_TORCH_THREADS", "4"))))
        # Keep inter-op parallelism bounded; repeated tiny worker pools can slow
        # CPU-only inference and oversubscribe GitHub's hosted runner.
        try:
            torch.set_num_interop_threads(max(1, int(os.getenv("CHATTERBOX_TORCH_INTEROP_THREADS", "1"))))
        except RuntimeError:
            # PyTorch only permits setting this before its first inter-op work.
            pass
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

def _chatterbox_language_id(text, language):
    """Select the Chatterbox multilingual language from the phrase's actual script.

    Hinglish is authored with Hindi words in Devanagari and English words/terms
    in Latin script. Chatterbox accepts one language_id per phrase, so Hindi-
    dominant phrases must use its Hindi model rather than being sent as English.
    """
    selected = str(language or "").strip().lower()
    if selected == "english":
        return "en"
    if selected == "hindi":
        return "hi"
    devanagari = sum(1 for char in str(text) if "\u0900" <= char <= "\u097f" and char.isalpha())
    # Any Hindi-script word is enough to require Hindi pronunciation. Latin-heavy
    # technical vocabulary must not cause the whole mixed phrase to be sent to
    # the English token, which can misread the Hindi words.
    return "hi" if devanagari else "en"


_LAST_CHATTERBOX_LANGUAGES = []


def _synth_chatterbox(text, language, path):
    import torchaudio
    model = _get_chatterbox()
    language_id = _chatterbox_language_id(text, language)
    audio = model.generate(text, language_id=language_id)
    torchaudio.save(str(path), audio.detach().cpu(), model.sr)
    return language_id


def _chatterbox_phrase_signature(sentence, language):
    """Stable cache key for one completed Chatterbox phrase."""
    payload = {
        "schema": 2,
        "model": os.getenv("CHATTERBOX_CACHE_MODEL_TAG", "multilingual-v3"),
        "language": _chatterbox_language_id(sentence, language),
        "text": str(sentence),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chatterbox_cached_phrase(audio_path, metadata_path, signature):
    """Return cached duration only when both the phrase fingerprint and WAV agree."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("signature") != signature:
            return None
        if not audio_path.is_file() or audio_path.stat().st_size < 1024:
            return None
        if int(metadata.get("size_bytes") or -1) != audio_path.stat().st_size:
            return None
        duration = float(metadata.get("duration_seconds") or 0)
        return duration if 0.2 <= duration <= 120.0 else None
    except (OSError, ValueError, TypeError):
        return None


def _synth_all_chatterbox(sentences, language, build_dir):
    """Synthesize with per-phrase atomic caching and visible progress.

    A documentary can take hours on a CPU-only runner. Persisting each completed
    phrase means an interrupted/retried Actions run does not repeat hours of TTS.
    The cache key includes the exact text, language and model tag; partial WAVs
    are never treated as completed audio.
    """
    cache_setting = os.getenv("CHATTERBOX_CACHE_DIR", "").strip()
    cache_dir = Path(cache_setting) if cache_setting else Path(build_dir) / "chatterbox_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    global _LAST_CHATTERBOX_LANGUAGES
    _LAST_CHATTERBOX_LANGUAGES = []
    wavs = []
    reused = 0
    total = len(sentences)
    try:
        heartbeat_seconds = max(10.0, float(os.getenv("CHATTERBOX_HEARTBEAT_SECONDS", "60") or 60))
    except ValueError:
        heartbeat_seconds = 60.0

    for i, sentence in enumerate(sentences, start=1):
        phrase_language = _chatterbox_language_id(sentence, language)
        _LAST_CHATTERBOX_LANGUAGES.append(phrase_language)
        signature = _chatterbox_phrase_signature(sentence, language)
        wav = cache_dir / f"{signature}.wav"
        metadata_path = cache_dir / f"{signature}.json"
        cached_duration = _chatterbox_cached_phrase(wav, metadata_path, signature)
        if cached_duration is not None:
            reused += 1
            print(
                f"Chatterbox narration {i}/{total}: reused cached phrase "
                f"({cached_duration:.1f}s; {reused}/{i} cached).",
                flush=True,
            )
            wavs.append(wav)
            continue

        temporary = cache_dir / f".{signature}.{os.getpid()}.partial.wav"
        metadata_temporary = cache_dir / f".{signature}.{os.getpid()}.partial.json"
        temporary.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
        started = time.monotonic()
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(heartbeat_seconds):
                elapsed = time.monotonic() - started
                print(
                    f"Chatterbox narration {i}/{total}: still synthesizing "
                    f"({len(str(sentence))} characters; {elapsed / 60.0:.1f} min elapsed).",
                    flush=True,
                )

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        print(
            f"Chatterbox narration {i}/{total}: starting phrase "
            f"({len(str(sentence))} characters).",
            flush=True,
        )
        heartbeat_thread.start()
        try:
            _synth_chatterbox(sentence, language, temporary)
        except Exception:
            temporary.unlink(missing_ok=True)
            metadata_temporary.unlink(missing_ok=True)
            raise
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

        try:
            duration = _dur(temporary)
            size_bytes = temporary.stat().st_size
            if duration < 0.2 or duration > 120.0 or size_bytes < 1024:
                raise RuntimeError(
                    f"Chatterbox phrase {i}/{total} produced invalid audio "
                    f"({duration:.2f}s, {size_bytes} bytes)."
                )
            os.replace(temporary, wav)
            metadata_temporary.write_text(
                json.dumps({
                    "schema": 1,
                    "signature": signature,
                    "duration_seconds": round(duration, 3),
                    "size_bytes": wav.stat().st_size,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(metadata_temporary, metadata_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            metadata_temporary.unlink(missing_ok=True)
            raise

        elapsed = time.monotonic() - started
        print(
            f"Chatterbox narration {i}/{total}: completed in {elapsed / 60.0:.1f} min "
            f"({duration:.1f}s audio; cache {reused}/{i}).",
            flush=True,
        )
        wavs.append(wav)

    print(
        f"Chatterbox narration complete: {total} phrases; "
        f"{reused} reused from cache; {total - reused} synthesized.",
        flush=True,
    )
    return wavs

# Alternatives when the configured Edge voice is unavailable. Every list holds one gender only:
# a male presenter must never be given a female voice (production build 43691314957 was), and
# the reverse. A build that cannot keep the requested gender falls through to the next
# narration provider or fails; it does not change the speaker silently.
_EDGE_FALLBACK_VOICES = {
    ("english", "male"): ["en-IN-PrabhatNeural", "en-US-GuyNeural", "en-GB-RyanNeural"],
    ("english", "female"): ["en-IN-NeerjaNeural", "en-US-AriaNeural", "en-GB-SoniaNeural"],
    ("other", "male"): ["hi-IN-MadhurNeural", "en-IN-PrabhatNeural"],
    ("other", "female"): ["hi-IN-SwaraNeural", "hi-IN-AnanyaNeural", "en-IN-NeerjaNeural"],
}


def _edge_voice_candidates(language, gender):
    """The configured voice first, then other Edge voices of the same gender."""
    preferred = pick_voice(language, gender)
    key = ("english" if language == "english" else "other", "female" if gender == "female" else "male")
    return list(dict.fromkeys([preferred, *_EDGE_FALLBACK_VOICES[key]]))


def _same_voice_passes():
    """How often one voice is tried before another is used: ``EDGE_TTS_VOICE_PASSES`` (4).

    With the default waits (20, 40 and 80 s) one voice rides out an outage of over two minutes.
    """
    try:
        return max(1, int(float(os.getenv("EDGE_TTS_VOICE_PASSES", "4") or 4)))
    except ValueError:
        return 4


def _voice_retry_wait(attempt):
    """Seconds to wait before trying the same voice again: ``EDGE_TTS_VOICE_RETRY_SECONDS`` (20), doubling."""
    try:
        base = max(0.0, float(os.getenv("EDGE_TTS_VOICE_RETRY_SECONDS", "20") or 20))
    except ValueError:
        base = 20.0
    return base * (2 ** max(0, attempt - 1))


def _synth_all_edge(sentences, language, gender, build_dir):
    """Synthesize the complete narration with one voice; never mix voices mid-video.

    A phrase that fails is tried again with the same voice after a wait, keeping the phrases
    already spoken, before any other voice is considered: a short service outage must not
    change the speaker.
    """
    import time
    global _LAST_EDGE_VOICE, _LAST_EDGE_FAILURES
    _LAST_EDGE_VOICE = None
    _LAST_EDGE_FAILURES = []
    _edge_module()  # fail once, immediately, instead of once per voice and retry
    last_error = None
    passes = _same_voice_passes()
    for voice in _edge_voice_candidates(language, gender):
        wavs = []
        failed_index = None
        for attempt in range(1, passes + 1):
            try:
                for i in range(len(wavs), len(sentences)):
                    sentence = sentences[i]
                    if i == failed_index and attempt == passes and passes > 1:
                        # Last try with this voice: the same words without symbols, in case the
                        # service refuses a character rather than being unavailable.
                        plain = _plain_speech_text(sentence)
                        if plain and plain != sentence:
                            print(f"Edge voice {voice}: phrase {i + 1} is sent again as plain text.")
                            sentence = plain
                    mp3 = build_dir / f"s{i}.mp3"
                    wav = build_dir / f"s{i}.wav"
                    mp3.unlink(missing_ok=True)
                    wav.unlink(missing_ok=True)
                    rate, pitch = _prosody(i, len(sentences), sentences[i])
                    asyncio.run(_synth_edge(sentence, voice, rate, pitch, mp3))
                    _to_wav(mp3, wav)
                    if not wav.exists() or wav.stat().st_size == 0:
                        raise RuntimeError(f"Edge TTS produced an empty WAV for chunk {i + 1}.")
                    wavs.append(wav)
                print(f"Edge TTS narration completed with voice {voice}.")
                _LAST_EDGE_VOICE = voice
                return wavs
            except VoiceProviderUnavailable:
                raise
            except Exception as exc:
                last_error = exc
                failed_index = len(wavs)
                if attempt < passes:
                    wait = _voice_retry_wait(attempt)
                    print(f"WARNING: Edge voice {voice} failed at phrase {len(wavs) + 1} of {len(sentences)} "
                          f"({exc}); trying the same voice again in {wait:.0f} s.")
                    time.sleep(wait)
        # Which phrase stopped which voice, and why: without this a failed build cannot be
        # told apart from an outage (production build 43702915253 named the last voice only).
        stopped = min(len(wavs), len(sentences) - 1)
        excerpt = " ".join(str(sentences[stopped]).split())[:32]
        _LAST_EDGE_FAILURES.append(
            f"{voice} stopped at phrase {stopped + 1}/{len(sentences)} \"{excerpt}\" "
            f"({_short_error(last_error)})")
        print(f"WARNING: Edge voice {voice} failed {passes} times; trying another voice "
              f"of the same gender: {last_error}")
        for i in range(len(sentences)):
            (build_dir / f"s{i}.mp3").unlink(missing_ok=True)
            (build_dir / f"s{i}.wav").unlink(missing_ok=True)
    raise RuntimeError("All Edge TTS voices failed: " + "; ".join(_LAST_EDGE_FAILURES)) from last_error


def _synth_all_espeak(sentences, language, build_dir):
    """Offline last-resort narration; requires espeak-ng installed by CI."""
    import shutil
    if shutil.which("espeak-ng") is None:
        raise VoiceProviderUnavailable(
            "No narration provider is available: the offline fallback espeak-ng is not "
            "installed. Install edge-tts (pip install edge-tts) for the primary provider, "
            "or espeak-ng (apt-get install espeak-ng) for the offline fallback.")
    voice = "en" if language in {"english", "hinglish"} else "hi"
    wavs = []
    for i, sentence in enumerate(sentences):
        wav = build_dir / f"s{i}.wav"
        wav.unlink(missing_ok=True)
        subprocess.run(
            ["espeak-ng", "-v", voice, "-s", os.getenv("ESPEAK_RATE", "155"), "-a", "175", "-w", str(wav), sentence],
            check=True, capture_output=True, text=True,
         timeout=tool_timeout())
        if not wav.exists() or wav.stat().st_size < 128:
            raise RuntimeError(f"espeak-ng produced empty audio for chunk {i + 1}.")
        wavs.append(wav)
    print(f"Offline espeak-ng fallback completed using voice {voice}.")
    return wavs

def _nvidia_magpie_voice(language, gender):
    import requests
    override = os.getenv("NVIDIA_TTS_VOICE", "").strip()
    if override:
        return override
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise VoiceProviderUnavailable("NVIDIA Magpie requires NVIDIA_API_KEY.")
    endpoint = "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com"
    response = requests.get(endpoint + "/v1/audio/list_voices",
                            headers={"Authorization": f"Bearer {api_key}"},
                            timeout=(15, 45))
    response.raise_for_status()
    payload = response.json()
    locale = "HI-IN" if language in {"hindi", "hinglish"} else "EN-US"
    voices = []
    def collect(value):
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            voices.extend(str(item) for item in value if isinstance(item, str))
    collect(payload)
    candidates = [voice for voice in voices if "Magpie-Multilingual." + locale in voice]
    if not candidates:
        raise RuntimeError(f"NVIDIA Magpie has no {locale} voices in its available voice list.")
    gender_terms = ("female", "woman", "aria", "mia", "isabela", "sofia", "louise") if gender == "female" else ("male", "man", "jason", "diego", "leo", "pascal", "ray", "siwei", "long", "houzhen")
    matching = [voice for voice in candidates if any(term in voice.lower() for term in gender_terms)]
    return (matching or candidates)[0]


def _synth_all_magpie(sentences, language, gender, build_dir):
    import requests
    """Synthesize a complete narration using NVIDIA's hosted Magpie HTTP endpoint."""
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise VoiceProviderUnavailable("NVIDIA Magpie requires NVIDIA_API_KEY.")
    endpoint = "https://877104f7-e885-42b9-8de8-f6e4c6303969.invocation.api.nvcf.nvidia.com"
    voice_name = _nvidia_magpie_voice(language, gender)
    language_code = "hi-IN" if language in {"hindi", "hinglish"} else "en-US"
    wavs = []
    for index, sentence in enumerate(sentences):
        response = requests.post(
            endpoint + "/v1/audio/synthesize",
            headers={"Authorization": f"Bearer {api_key}"},
            files={key: (None, value) for key, value in {
                "text": str(sentence), "language": language_code, "voice": voice_name,
                "encoding": "LINEAR_PCM", "sample_rate_hz": "22050",
            }.items()},
            timeout=(20, 180),
        )
        if response.status_code != 200:
            raise RuntimeError(f"NVIDIA Magpie HTTP {response.status_code}: {' '.join(response.text.split())[:200]}")
        wav = Path(build_dir) / f"s{index}.wav"
        temporary = wav.with_suffix(".nvidia-tmp.wav")
        temporary.write_bytes(response.content)
        if len(response.content) < 1024 or response.content[:4] != b"RIFF":
            temporary.unlink(missing_ok=True)
            raise RuntimeError("NVIDIA Magpie returned an invalid WAV response.")
        os.replace(temporary, wav)
        wavs.append(wav)
    print(f"NVIDIA Magpie narration completed with voice {voice_name}.")
    return wavs, voice_name


def synthesise(text, build_dir, language="hindi", gender="male"):
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    sentences = _speech_chunks(_apply_pronunciation_map(text), language=language)
    if not sentences:
        raise ValueError("No narration text was provided.")
    # Pause after the phrase that was just spoken (not the next phrase).
    pauses = [_pause_after(chunk) for chunk in sentences[:-1]] + [0.0]

    global _LAST_EDGE_FAILURES, _LAST_CHATTERBOX_LANGUAGES
    _LAST_EDGE_FAILURES = []
    _LAST_CHATTERBOX_LANGUAGES = []
    requested_engine = os.getenv("TTS_ENGINE", "chatterbox").strip().lower()
    if os.getenv("NVIDIA_TTS_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
        selected_tts_model = os.getenv("NVIDIA_TTS_MODEL", "magpie-tts-multilingual").strip()
        if selected_tts_model != "magpie-tts-multilingual":
            raise VoiceProviderUnavailable(f"Unsupported NVIDIA_TTS_MODEL: {selected_tts_model}")
        requested_engine = "nvidia_magpie"
    engine = requested_engine
    fallback_reason = None
    require_requested_engine = (os.getenv("TTS_REQUIRE_ENGINE", "").strip().lower() == requested_engine
                               or requested_engine == "chatterbox")
    is_ci = os.getenv("CI", "").strip().lower() == "true" or os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"
    allow_chatterbox_ci = os.getenv("ALLOW_CHATTERBOX_ON_CI", "").strip().lower() == "true"
    if engine == "chatterbox" and is_ci and not allow_chatterbox_ci:
        raise VoiceProviderUnavailable(
            "Chatterbox is the required narration engine, but CI inference was not explicitly enabled; "
            "refusing to switch to Edge TTS. Set ALLOW_CHATTERBOX_ON_CI=true in the workflow.")
    if require_requested_engine and engine != requested_engine:
        raise VoiceProviderUnavailable(
            f"Required narration engine '{requested_engine}' is not enabled in this environment; refusing fallback.")

    # Provider cascade: requested engine -> multiple Edge voices -> offline espeak-ng.
    # A transient network outage must not abort a documentary after script generation.
    magpie_voice = None
    if engine == "nvidia_magpie":
        try:
            wavs, magpie_voice = _synth_all_magpie(sentences, language, gender, build_dir)
        except Exception as magpie_exc:
            fallback_reason = f"NVIDIA Magpie failed ({type(magpie_exc).__name__}: {magpie_exc}); trying Edge TTS."
            if require_requested_engine:
                raise VoiceProviderUnavailable(f"Required narration engine NVIDIA Magpie failed: {magpie_exc}") from magpie_exc
            print("WARNING: " + fallback_reason)
            try:
                wavs = _synth_all_edge(sentences, language, gender, build_dir)
                engine = "edge"
            except Exception as edge_exc:
                fallback_reason += f" Edge TTS also failed ({type(edge_exc).__name__}: {edge_exc}); using offline espeak-ng."
                wavs = _synth_all_espeak(sentences, language, build_dir)
                engine = "espeak-ng"
    elif engine == "chatterbox":
        try:
            wavs = _synth_all_chatterbox(sentences, language, build_dir)
        except Exception as exc:
            fallback_reason = f"Chatterbox failed ({type(exc).__name__}: {exc}); trying Edge TTS."
            if require_requested_engine:
                raise VoiceProviderUnavailable(
                    f"Required narration engine 'chatterbox' failed; refusing provider fallback: {exc}") from exc
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
            if str(edge_exc).startswith("All Edge TTS voices failed"):
                fallback_reason = f"{edge_exc}; using offline espeak-ng."
            else:
                fallback_reason = (f"All Edge TTS voices failed ({type(edge_exc).__name__}: {edge_exc}); "
                                   "using offline espeak-ng.")
            print("WARNING: " + fallback_reason)
            wavs = _synth_all_espeak(sentences, language, build_dir)
            engine = "espeak-ng"

    # Every GitHub Actions production pipeline must use neural narration. Offline
    # espeak-ng is useful for local diagnostics, but its robotic delivery is not
    # acceptable in a published Short or documentary. A workflow may explicitly
    # opt out only for a non-release diagnostic job with TTS_REJECT_ROBOTIC_FALLBACK=0.
    reject_robotic = os.getenv("TTS_REJECT_ROBOTIC_FALLBACK", "").strip().lower()
    reject_robotic = reject_robotic in {"1", "true", "yes"} or (
        not reject_robotic and is_ci
    )
    if engine == "espeak-ng" and reject_robotic:
        raise VoiceProviderUnavailable(
            "Production narration refused: neural TTS was unavailable and the offline robotic voice is disabled. "
            "Restore a neural provider (Chatterbox, NVIDIA Magpie, or Edge TTS) before rendering.")

    # Chatterbox has no native rate argument in this integration. Apply a
    # subtle 4% tempo lift only to that engine; Edge already receives TTS_RATE.
    playback_speed = float(os.getenv("CHATTERBOX_PLAYBACK_SPEED", "1.04")) if engine == "chatterbox" else 1.0
    playback_speed = max(0.95, min(playback_speed, 1.10))
    durations = [_dur(wav) / playback_speed for wav in wavs]
    completeness = check_narration_complete(sentences, durations)
    out = build_dir / "voice.wav"
    _join(wavs, pauses, out, speed=playback_speed)
    timings, current = [], 0.0
    for duration, pause, sentence in zip(durations, pauses, sentences):
        timings.append((current, current + duration, sentence))
        current += duration + pause
    report = {
        "requested_engine": requested_engine,
        "actual_engine": engine,
        "fallback_reason": fallback_reason,
        "voice": (_LAST_EDGE_VOICE or pick_voice(language, gender)) if engine == "edge" else (magpie_voice if engine == "nvidia_magpie" else ("Chatterbox multilingual" if engine == "chatterbox" else "espeak-ng offline fallback")),
        "configured_voice": pick_voice(language, gender),
        "voice_failures": list(_LAST_EDGE_FAILURES),
        "voice_changed": bool(engine == "edge" and _LAST_EDGE_VOICE
                              and _LAST_EDGE_VOICE != pick_voice(language, gender)),
        "providers_available": provider_status(),
        "language": language,
        "chatterbox_phrase_languages": list(_LAST_CHATTERBOX_LANGUAGES),
        "hinglish_language_policy": (
            "Hindi-script phrases use the Hindi multilingual token; English-only phrases use English. "
            "Keep Hindi words in Devanagari and English terms in Latin script."
            if str(language).lower() == "hinglish" else None
        ),
        "gender": gender,
        "chunk_count": len(sentences),
        "characters": completeness["characters"],
        "words": completeness["words"],
        "characters_per_second": completeness["characters_per_second"],
        "speech_words_per_minute": completeness["words_per_minute"],
        "speech_seconds": round(sum(durations), 3),
        "inserted_pause_seconds": round(sum(pauses), 3),
        "assembled_duration_seconds": round(_dur(out), 3),
        "playback_speed": playback_speed,
        "target_delivery": "slightly_faster_professional_documentary",
        "human_voice_review": "pending",
        "notice": "Technical metadata does not certify naturalness, emotion, pronunciation or professional performance; listen to the complete narration.",
    }
    (build_dir / "narration_synthesis_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out, timings


from core.monitoring import monitored as _monitored
from core.ffmpeg_utils import PROBE_TIMEOUT, render_timeout, tool_timeout
_synthesise_raw = synthesise
synthesise = _monitored("tts", "synthesise", _synthesise_raw)
