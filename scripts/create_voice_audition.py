"""Create a same-script English news narration comparison: Edge TTS vs Chatterbox.

The two tracks use identical wording and are rendered separately. Chatterbox is
marked unsuccessful if the voice provider silently falls back to Edge/espeak.
"""
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

from core.voice import synthesise

TEXT = """Breaking news. India's northern states are preparing for a sharp change in weather this week, with forecasts warning of heavy rain in some areas and a fresh spell of snowfall in the higher Himalayas. Officials are asking residents and travellers to check local advisories before setting out. The key question is how quickly conditions could change, especially on mountain roads where visibility and temperatures can drop within hours. We will continue tracking verified updates as authorities release more information."""

CANDIDATES = [
    {
        "id": "edge_indian_english",
        "engine": "edge",
        "voice": "en-IN-PrabhatNeural",
        "description": "Microsoft Edge Neural, Indian English male",
        "gender": "male",
    },
    {
        "id": "chatterbox_multilingual",
        "engine": "chatterbox",
        "voice": "Chatterbox multilingual default",
        "description": "Chatterbox multilingual model, CPU inference",
        "gender": "male",
    },
]


def render_candidate(candidate: dict, root: Path) -> dict:
    folder = root / candidate["id"]
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    os.environ["TTS_ENGINE"] = candidate["engine"]
    os.environ["TTS_VOICE"] = candidate["voice"] if candidate["engine"] == "edge" else ""
    os.environ["TTS_RATE"] = "+13%"
    os.environ["TTS_OPENING_RATE"] = "+12%"
    os.environ["TTS_CLOSING_RATE"] = "+10%"
    os.environ["TTS_PAUSE_SCALE"] = "0.55"
    os.environ["TTS_REJECT_ROBOTIC_FALLBACK"] = "1"
    os.environ["CHATTERBOX_TORCH_THREADS"] = "2"
    os.environ["CHATTERBOX_PLAYBACK_SPEED"] = "1.04"
    os.environ["ALLOW_CHATTERBOX_ON_CI"] = "true"

    started = time.perf_counter()
    result = {
        "id": candidate["id"],
        "requested_engine": candidate["engine"],
        "description": candidate["description"],
        "voice": candidate["voice"],
        "status": "failed",
    }
    try:
        audio, _ = synthesise(TEXT, folder, language="english", gender=candidate["gender"])
        report_path = folder / "narration_synthesis_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        actual = report.get("actual_engine")
        result.update({
            "status": "success" if actual == candidate["engine"] else "failed_provider_fallback",
            "actual_engine": actual,
            "audio": str(audio),
            "audio_bytes": audio.stat().st_size if audio.exists() else 0,
            "duration_seconds": report.get("assembled_duration_seconds"),
            "speech_words_per_minute": report.get("speech_words_per_minute"),
            "speech_seconds": report.get("speech_seconds"),
            "runtime_seconds": round(time.perf_counter() - started, 2),
            "report": str(report_path),
            "fallback_reason": report.get("fallback_reason"),
        })
    except Exception as exc:
        result.update({
            "error": f"{type(exc).__name__}: {exc}",
            "runtime_seconds": round(time.perf_counter() - started, 2),
        })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="daily_news_voice_audition")
    args = parser.parse_args(argv)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)

    results = [render_candidate(candidate, root) for candidate in CANDIDATES]
    index = {
        "topic": "English daily-news narration comparison",
        "script_language": "English",
        "script": TEXT,
        "same_script_for_all_candidates": True,
        "candidates": results,
        "selection_guidance": "Listen to both complete WAV files. Compare pronunciation, naturalness, pacing, clarity and fatigue. Runtime is measured on the GitHub Actions CPU runner.",
        "limitations": [
            "Technical metrics cannot judge whether a voice sounds human or emotionally convincing.",
            "Chatterbox may be slower or fail under the memory limits of a hosted CPU runner.",
            "No production voice setting is changed by this audition."
        ],
    }
    (root / "audition_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))
    # Preserve the artifact even if Chatterbox cannot run; the report records that honestly.
    if not any(row["status"] == "success" for row in results):
        raise SystemExit("No narration candidate completed successfully; inspect audition_index.json")


if __name__ == "__main__":
    main()
