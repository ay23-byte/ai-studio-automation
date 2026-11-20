"""Create an isolated English Daily News voice comparison: Edge vs Chatterbox."""
from __future__ import annotations
import argparse, json, os, shutil, sys, time
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.voice import synthesise

TEXT = """Breaking news. India's northern states are preparing for a sharp change in weather this week, with forecasts warning of heavy rain in some areas and a fresh spell of snowfall in the higher Himalayas. Officials are asking residents and travellers to check local advisories before setting out. The key question is how quickly conditions could change, especially on mountain roads where visibility and temperatures can drop within hours. We will continue tracking verified updates as authorities release more information."""

CANDIDATES = [
    {"id":"edge_indian_english","engine":"edge","voice":"en-IN-PrabhatNeural","description":"Edge Neural, Indian English male","gender":"male"},
    {"id":"chatterbox_multilingual","engine":"chatterbox","voice":"Chatterbox multilingual","description":"Chatterbox multilingual, CPU inference","gender":"male"},
]

def render(candidate: dict, root: Path) -> dict:
    folder = root / candidate["id"]
    if folder.exists(): shutil.rmtree(folder)
    folder.mkdir(parents=True)
    os.environ.update({
        "TTS_ENGINE":candidate["engine"],
        "TTS_VOICE":candidate["voice"] if candidate["engine"]=="edge" else "",
        "TTS_RATE":"+13%","TTS_OPENING_RATE":"+12%","TTS_CLOSING_RATE":"+10%",
        "TTS_PAUSE_SCALE":"0.55","TTS_REJECT_ROBOTIC_FALLBACK":"1",
        "CHATTERBOX_TORCH_THREADS":"2","CHATTERBOX_PLAYBACK_SPEED":"1.04",
        "ALLOW_CHATTERBOX_ON_CI":"true",
    })
    started=time.perf_counter()
    row={"id":candidate["id"],"requested_engine":candidate["engine"],
         "description":candidate["description"],"voice":candidate["voice"],"status":"failed"}
    try:
        audio,_=synthesise(TEXT,folder,language="english",gender=candidate["gender"])
        report_path=folder/"narration_synthesis_report.json"
        report=json.loads(report_path.read_text(encoding="utf-8"))
        actual=report.get("actual_engine")
        row.update({"status":"success" if actual==candidate["engine"] else "failed_provider_fallback",
                    "actual_engine":actual,"audio":str(audio),
                    "audio_bytes":audio.stat().st_size if audio.exists() else 0,
                    "duration_seconds":report.get("assembled_duration_seconds"),
                    "speech_words_per_minute":report.get("speech_words_per_minute"),
                    "runtime_seconds":round(time.perf_counter()-started,2),
                    "report":str(report_path),"fallback_reason":report.get("fallback_reason")})
    except Exception as exc:
        row.update({"error":f"{type(exc).__name__}: {exc}",
                    "runtime_seconds":round(time.perf_counter()-started,2)})
    return row

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="daily_news_voice_audition")
    root=Path(parser.parse_args(argv).output_dir)
    root.mkdir(parents=True,exist_ok=True)
    results=[render(candidate,root) for candidate in CANDIDATES]
    index={"topic":"English Daily News narration comparison","script_language":"English",
           "script":TEXT,"same_script_for_all_candidates":True,"candidates":results,
           "selection_guidance":"Listen to both complete WAV files. Compare pronunciation, naturalness, pacing, clarity and listening fatigue. Runtime is measured on the GitHub Actions CPU runner.",
           "limitations":["Technical metrics cannot judge naturalness or emotional delivery.",
                         "A Chatterbox fallback to Edge is recorded as a failure, not a Chatterbox win.",
                         "This audition does not modify any production voice setting."]}
    (root/"audition_index.json").write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(index,ensure_ascii=False,indent=2))
    if not any(row["status"]=="success" for row in results):
        raise SystemExit("No candidate completed successfully; inspect audition_index.json")

if __name__=="__main__":
    main()
