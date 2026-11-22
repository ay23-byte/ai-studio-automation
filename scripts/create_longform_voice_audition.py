"""Generate a short, same-script long-form voice audition without changing production."""
from __future__ import annotations
import argparse, json, os, shutil, sys, time
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.voice import synthesise

TEXT = """Imagine standing beside a lake at dawn. The water looks calm, the air is still, and nothing seems unusual. But deep beneath the surface, an invisible danger may be building. In this documentary, we will follow the evidence carefully, separate what scientists know from what remains uncertain, and explain the mechanism step by step. Take a moment with each idea. The most important details are often hidden in the quiet parts of the story."""

CANDIDATES = [
    {"id":"longform_chatterbox_relaxed","engine":"chatterbox","voice":"Chatterbox multilingual V3, relaxed pacing","gender":"male"},
    {"id":"edge_christopher_slow","engine":"edge","voice":"en-US-ChristopherNeural","gender":"male"},
]

def render(candidate: dict, root: Path) -> dict:
    folder=root/candidate["id"]
    if folder.exists(): shutil.rmtree(folder)
    folder.mkdir(parents=True)
    os.environ.update({
        "TTS_ENGINE":candidate["engine"],"TTS_REQUIRE_ENGINE":candidate["engine"],
        "TTS_VOICE":candidate["voice"] if candidate["engine"]=="edge" else "",
        "TTS_RATE":"-3%","TTS_OPENING_RATE":"-4%","TTS_CLOSING_RATE":"-3%",
        "TTS_PAUSE_SCALE":"1.4","TTS_REJECT_ROBOTIC_FALLBACK":"1",
        "CHATTERBOX_TORCH_THREADS":"2","CHATTERBOX_TORCH_INTEROP_THREADS":"1",
        "CHATTERBOX_PLAYBACK_SPEED":"0.98","VOICE_COMPRESSOR_RATIO":"2.0",
        "VOICE_COMPRESSOR_MAKEUP_DB":"1.0","ALLOW_CHATTERBOX_ON_CI":"true",
    })
    started=time.perf_counter()
    row={"id":candidate["id"],"requested_engine":candidate["engine"],"voice":candidate["voice"],"status":"failed"}
    try:
        audio,_=synthesise(TEXT,folder,language="english",gender=candidate["gender"])
        report=json.loads((folder/"narration_synthesis_report.json").read_text(encoding="utf-8"))
        actual=report.get("actual_engine")
        row.update({"status":"success" if actual==candidate["engine"] else "failed_provider_fallback",
                    "actual_engine":actual,"audio":str(audio),
                    "audio_bytes":audio.stat().st_size if audio.exists() else 0,
                    "duration_seconds":report.get("assembled_duration_seconds"),
                    "speech_words_per_minute":report.get("speech_words_per_minute"),
                    "runtime_seconds":round(time.perf_counter()-started,2),
                    "report":str(folder/"narration_synthesis_report.json"),
                    "fallback_reason":report.get("fallback_reason")})
    except Exception as exc:
        row.update({"error":f"{type(exc).__name__}: {exc}","runtime_seconds":round(time.perf_counter()-started,2)})
    return row

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",default="longform_voice_audition")
    root=Path(parser.parse_args(argv).output_dir);root.mkdir(parents=True,exist_ok=True)
    results=[render(c,root) for c in CANDIDATES]
    index={"purpose":"Short same-script long-form voice audition","script":TEXT,"candidates":results,
           "selection_guidance":"Listen to both samples. Compare naturalness, pacing, warmth, pronunciation and listening fatigue. Do not select based on duration metrics alone.",
           "production_changes_tested":["0.98 Chatterbox tempo","1.4x phrase pause scale","gentler compressor"],
           "limitations":["Short sample, not a full documentary guarantee.","Technical metrics cannot assess subjective voice quality."]}
    (root/"audition_index.json").write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(index,ensure_ascii=False,indent=2))
    if not any(r["status"]=="success" for r in results): raise SystemExit("No audition candidate completed.")

if __name__=="__main__": main()
