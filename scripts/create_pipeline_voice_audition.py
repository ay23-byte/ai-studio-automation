"""Generate real voice samples for General Shorts, Daily News and documentaries."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audio_quality import inspect_narration
from core.voice import synthesise

SAMPLES = {
    "general_shorts": (
        "कभी आपने notice किया है कि आपका phone एक second में लाखों bits of data receive कर सकता है? "
        "इसकी एक बड़ी वजह है optical fibre, जहाँ information light pulses के रूप में travel करती है।"
    ),
    "daily_news": (
        "आज मौसम की खबर में सबसे अहम बात सिर्फ़ बारिश नहीं है। "
        "बदलते temperature और तेज़ हवाओं का असर transport, farming और रोज़मर्रा की ज़िंदगी पर भी पड़ सकता है।"
    ),
    "documentary": (
        "किसी भी बड़ी scientific discovery के पीछे सिर्फ़ एक brilliant idea नहीं होता। "
        "उसके पीछे सालों की measurement, failed experiments और engineers की मेहनत होती है। "
        "आज हम समझेंगे कि एक नई technology हमारी दुनिया को कैसे बदल सकती है।"
    ),
}

CANDIDATES = (
    {"id": "chatterbox_multilingual_v3", "engine": "chatterbox",
     "voice": "Chatterbox multilingual V3", "tts_voice": ""},
    {"id": "edge_hindi_madhur", "engine": "edge",
     "voice": "hi-IN-MadhurNeural", "tts_voice": "hi-IN-MadhurNeural"},
)


def render_candidate(pipeline: str, text: str, candidate: dict, root: Path) -> dict:
    folder = root / pipeline / candidate["id"]
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "TTS_ENGINE": candidate["engine"],
        "TTS_REQUIRE_ENGINE": candidate["engine"],
        "TTS_VOICE": candidate["tts_voice"],
        "NVIDIA_TTS_ENABLED": "0",
        "TTS_REJECT_ROBOTIC_FALLBACK": "1",
        "TTS_PAUSE_SCALE": "1.0",
        "TTS_RATE": "-2%",
        "TTS_OPENING_RATE": "-3%",
        "TTS_CLOSING_RATE": "-2%",
        "TTS_PITCH": "+0Hz",
        "CHATTERBOX_PLAYBACK_SPEED": "1.00",
        "CHATTERBOX_TORCH_THREADS": "2",
        "CHATTERBOX_TORCH_INTEROP_THREADS": "1",
        "ALLOW_CHATTERBOX_ON_CI": "true",
        "VOICE_COMPRESSOR_RATIO": "2.0",
        "VOICE_COMPRESSOR_MAKEUP_DB": "1.0",
    })
    started = time.perf_counter()
    row = {"pipeline": pipeline, "candidate": candidate["id"],
           "requested_engine": candidate["engine"], "requested_voice": candidate["voice"],
           "status": "failed"}
    try:
        audio, _ = synthesise(text, folder, language="hinglish", gender="male")
        report_path = folder / "narration_synthesis_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        actual = report.get("actual_engine")
        if actual != candidate["engine"]:
            raise RuntimeError(f"Provider mismatch: requested {candidate['engine']}, actual {actual}")
        screening = inspect_narration(audio, output_dir=folder)
        row.update({
            "status": "success", "actual_engine": actual, "actual_voice": report.get("voice"),
            "audio": str(audio), "audio_bytes": audio.stat().st_size,
            "duration_seconds": report.get("assembled_duration_seconds"),
            "speech_words_per_minute": report.get("speech_words_per_minute"),
            "phrase_languages": report.get("chatterbox_phrase_languages", []),
            "narration_screening": screening, "report": str(report_path),
            "runtime_seconds": round(time.perf_counter() - started, 2),
            "human_listening_review": "pending",
        })
    except Exception as exc:
        row.update({"error": f"{type(exc).__name__}: {exc}",
                    "runtime_seconds": round(time.perf_counter() - started, 2)})
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="pipeline_voice_audition")
    root = Path(parser.parse_args(argv).output_dir)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for pipeline, sample in SAMPLES.items():
        for candidate in CANDIDATES:
            print(f"Generating actual narration: {pipeline} / {candidate['id']}", flush=True)
            rows.append(render_candidate(pipeline, sample, candidate, root))
    index = {
        "purpose": "Actual Hinglish narration audition across all three production pipelines",
        "script_language": "hinglish",
        "script_convention": "Hindi words in Devanagari; English terms and technical words in Latin script",
        "same_script_per_pipeline": True,
        "samples": SAMPLES,
        "candidates": rows,
        "selection_guidance": (
            "Listen to every WAV. Compare Hindi pronunciation, English technical-word pronunciation, "
            "natural code-switching, warmth, emphasis, pacing, consonant clarity and listening fatigue. "
            "Do not select a provider from loudness or duration metrics alone."
        ),
        "limitations": [
            "Automated screening measures technical noise and level only; it cannot certify human-like delivery.",
            "Human listening review is required before changing the production voice provider.",
            "These are short representative auditions, not a guarantee of full-video performance.",
        ],
    }
    (root / "audition_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))
    if not any(row["status"] == "success" for row in rows):
        raise SystemExit("No actual narration candidate completed successfully.")


if __name__ == "__main__":
    main()
