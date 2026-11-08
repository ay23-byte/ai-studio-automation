"""Fail-closed technical, visual and audio QA for a rendered vertical Short."""
import argparse, json, subprocess
from pathlib import Path

def _sample_video_frames(video, duration, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    for index, fraction in enumerate((0.15, 0.50, 0.85), start=1):
        target = output_dir / f"short_sample_{index}.jpg"
        result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{duration*fraction:.3f}", "-i", str(video), "-frames:v", "1",
            "-q:v", "3", str(target)], capture_output=True, text=True, check=False, timeout=60)
        if result.returncode == 0 and target.is_file() and target.stat().st_size > 1000:
            samples.append(str(target))
    return samples

def run(video, report_dir=None, manifest_path=None):
    video = Path(video)
    data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams",
        "-show_format", "-of", "json", str(video)], text=True))
    vs = [s for s in data["streams"] if s["codec_type"] == "video"]
    aus = [s for s in data["streams"] if s["codec_type"] == "audio"]
    errors, warnings = [], []
    if len(vs) != 1 or len(aus) != 1:
        return {"status":"FAIL","errors":["exactly one video and one audio stream required"],
                "warnings":[],"publication_ready":False}
    v, a = vs[0], aus[0]
    dur = float(data["format"]["duration"])
    try:
        n, d = map(int, v.get("avg_frame_rate", "0/1").split("/"))
        fps = n/d if d else 0
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0
    checks = [(v.get("width")==1080 and v.get("height")==1920,"resolution must be 1080x1920"),
      (29.9<=fps<=30.1,"frame rate must be 30 fps"),(35<=dur<=58,"duration must be 35–58 seconds"),
      (v.get("codec_name")=="h264","video must be H.264"),(v.get("pix_fmt")=="yuv420p","pixel format must be yuv420p"),
      (a.get("codec_name")=="aac","audio must be AAC"),(int(a.get("sample_rate",0))==48000,"audio must be 48 kHz")]
    errors += [message for ok,message in checks if not ok]
    for filt,label,marker in [("blackdetect=d=0.5:pix_th=0.10:pic_th=0.98","black frames","black_start:"),
                               ("silencedetect=n=-45dB:d=0.5","silence","silence_start:")]:
        result = subprocess.run(["ffmpeg","-hide_banner","-i",str(video),
            "-vf" if label=="black frames" else "-af",filt,"-f","null","-"],
            capture_output=True,text=True,check=False)
        if result.returncode != 0: warnings.append(f"{label} scan could not be completed.")
        elif marker in (result.stderr or ""): warnings.append(f"{label} detected; inspect timestamps.")
    if errors:
        return {"status":"FAIL","duration_seconds":round(dur,2),"fps":round(fps,2),
            "resolution":f'{v.get("width")}x{v.get("height")}',"errors":errors,"warnings":warnings,
            "human_review_required":True,"publication_ready":False}
    root = Path(report_dir) if report_dir else video.parent/"advanced_qa"
    root.mkdir(parents=True,exist_ok=True)
    from core.audio_quality import inspect_audio
    from core.visual_consistency import audit_visual_consistency
    semantic_manifest = Path(manifest_path) if manifest_path else video.parent/"production_manifest.json"
    if semantic_manifest.is_file():
        from core.news_short_caption_qa import audit_captions
        captions = audit_captions(semantic_manifest)
    else:
        captions = {"status":"REVIEW","reason":"No production manifest supplied; caption timing and narration alignment could not be audited.","human_review_required":True,"caption_alignment_verified":False}
    (root/"caption_qa_report.json").write_text(json.dumps(captions,ensure_ascii=False,indent=2),encoding="utf-8")
    if semantic_manifest.is_file():
        from core.news_short_presenter_qa import audit_presenter
        presenter = audit_presenter(semantic_manifest, video, root)
    else:
        presenter = {"status":"REVIEW","reason":"No production manifest supplied; presenter visibility could not be audited.",
                     "human_review_required":True,"presenter_visibility_verified":False,
                     "publication_ready":False}
    (root/"presenter_visibility_report.json").write_text(
        json.dumps(presenter,ensure_ascii=False,indent=2),encoding="utf-8")
    if presenter.get("status") == "FAIL":
        errors.append("3D presenter clip or final-frame visibility QA detected a blocking issue.")
    elif presenter.get("status") in {"REVIEW","NOT_RUN"}:
        warnings.append("3D presenter visibility and motion need human review; see presenter_visibility_report.json.")
    if captions.get("status") == "FAIL":
        errors.append("Caption timing or narration alignment QA detected a blocking issue.")
    elif captions.get("status") in {"REVIEW","NOT_RUN"}:
        warnings.append("Caption timing/layout needs human review; see caption_qa_report.json.")
    audio = inspect_audio(video)
    (root/"audio_quality_report.json").write_text(json.dumps(audio,ensure_ascii=False,indent=2),encoding="utf-8")
    frames = _sample_video_frames(video,dur,root/"sample_frames")
    visual = audit_visual_consistency({"shots":[{"shot_id":f"sample_{i+1}","sample_frames":[p]}
        for i,p in enumerate(frames)]},root)
    visual["sampled_frame_count"] = len(frames)
    visual["sampling_method"] = "three evenly spaced frames at 15%, 50%, and 85% of the export"
    visual["semantic_match_verified"] = False
    visual["human_visual_review_required"] = True
    (root/"visual_quality_report.json").write_text(json.dumps(visual,ensure_ascii=False,indent=2),encoding="utf-8")
    # The renderer writes this manifest beside the review MP4. Legacy exports without
    # a manifest remain technically scannable, but semantic QA is explicitly not run.
    semantic_manifest = Path(manifest_path) if manifest_path else video.parent/"production_manifest.json"
    semantic = {"status":"NOT_RUN","reason":"No production manifest supplied; shot-to-narration semantic QA was not run.",
                "human_review_required":True,"semantic_match_verified":False}
    if semantic_manifest.is_file():
        from core.news_semantic_qa import run_semantic_qa
        semantic = run_semantic_qa(video,semantic_manifest,root)
    (root/"semantic_qa_report.json").write_text(json.dumps(semantic,ensure_ascii=False,indent=2),encoding="utf-8")
    if semantic.get("status") == "FAIL":
        errors.append("Shot-to-narration semantic QA detected a mismatch or blocking error.")
    elif str(semantic.get("status","")).upper() in {"REVIEW","ERROR","UNAVAILABLE","NOT_RUN"} or str(semantic.get("status","")).lower() == "error":
        warnings.append("Shot-to-narration semantic QA needs human review; see semantic_qa_report.json.")
    if audio.get("status") in {"FAIL","ERROR"}: errors.append("Audio quality analysis failed or detected a blocking audio issue.")
    elif audio.get("status")=="REVIEW": warnings.append("Audio quality needs human review; see audio_quality_report.json.")
    if not frames or visual.get("summary",{}).get("measured_shot_count",0)==0:
        errors.append("Visual sampling/analysis could not inspect any rendered frames.")
    elif visual.get("status")=="FAIL": errors.append("Visual quality analysis detected a blocking frame-quality issue.")
    elif visual.get("status")=="REVIEW": warnings.append("Visual quality needs human review; see visual_quality_report.json.")
    warnings.extend(f"Audio: {item}" for item in audio.get("warnings",[]))
    warnings.extend(f"Visual: {item.get('message')}" for item in visual.get("issues",[]) if item.get("level")=="warning")
    return {"status":"FAIL" if errors else ("REVIEW" if warnings else "PASS"),
        "duration_seconds":round(dur,2),"fps":round(fps,2),"resolution":f'{v["width"]}x{v["height"]}',
        "errors":errors,"warnings":warnings,"audio_quality_report":str(root/"audio_quality_report.json"),
        "visual_quality_report":str(root/"visual_quality_report.json"),"semantic_qa_report":str(root/"semantic_qa_report.json"),
        "caption_qa_report":str(root/"caption_qa_report.json"),
        "caption_qa_status":captions.get("status"),
        "presenter_visibility_report":str(root/"presenter_visibility_report.json"),
        "presenter_visibility_status":presenter.get("status"),
        "semantic_qa_status":semantic.get("status"),"sampled_frame_count":len(frames),
        "human_review_required":True,"publication_ready":False,
        "notice":"A PASS is technical screening only. It does not verify facts, visual relevance, historical authenticity, media rights, captions, or policy compliance. Human review and phone playback remain mandatory."}

def exit_code_for_status(status):
    """Only blocking QA failures fail CI; REVIEW is a successful handoff to a human."""
    return 0 if str(status).upper() in {"PASS", "REVIEW"} else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("video")
    parser.add_argument("--report",default="output/daily_news/rendered/technical_qa.json")
    parser.add_argument("--manifest",default=None,help="Production manifest containing per-shot narration and visual descriptions")
    args=parser.parse_args()
    result=run(Path(args.video),Path(args.report).parent/"advanced_qa",args.manifest)
    Path(args.report).parent.mkdir(parents=True,exist_ok=True)
    Path(args.report).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2)); raise SystemExit(exit_code_for_status(result["status"]))
