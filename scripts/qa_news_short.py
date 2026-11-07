"""Fail-closed technical QA for a rendered vertical Short."""
import argparse, json, subprocess
from pathlib import Path

def run(video):
    data=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(video)],text=True))
    vs=[s for s in data["streams"] if s["codec_type"]=="video"]
    aus=[s for s in data["streams"] if s["codec_type"]=="audio"]
    errors=[]; warnings=[]
    if len(vs)!=1 or len(aus)!=1: errors.append("exactly one video and one audio stream required")
    if errors: return {"status":"FAIL","errors":errors,"warnings":warnings,"publication_ready":False}
    v,a=vs[0],aus[0]; dur=float(data["format"]["duration"])
    n,d=map(int,v.get("avg_frame_rate","0/1").split("/")); fps=n/d if d else 0
    checks=[(v["width"]==1080 and v["height"]==1920,"resolution must be 1080x1920"),
      (29.9<=fps<=30.1,"frame rate must be 30 fps"),(35<=dur<=58,"duration must be 35–58 seconds"),
      (v.get("codec_name")=="h264","video must be H.264"),(v.get("pix_fmt")=="yuv420p","pixel format must be yuv420p"),
      (a.get("codec_name")=="aac","audio must be AAC"),(int(a.get("sample_rate",0))==48000,"audio must be 48 kHz")]
    errors += [msg for ok,msg in checks if not ok]
    for filt, label in [("blackdetect=d=0.5:pix_th=0.10:pic_th=0.98","black frames"),("silencedetect=n=-45dB:d=0.5","silence")]:
      r=subprocess.run(["ffmpeg","-hide_banner","-i",str(video),"-vf" if label=="black frames" else "-af",filt,"-f","null","-"],capture_output=True,text=True)
      if ("black_start:" if label=="black frames" else "silence_start:") in r.stderr: warnings.append(label+" detected; inspect timestamps")
    result={"status":"PASS" if not errors else "FAIL","duration_seconds":round(dur,2),"fps":round(fps,2),
      "resolution":f'{v["width"]}x{v["height"]}',"errors":errors,"warnings":warnings,
      "human_review_required":True,"publication_ready":False}
    return result

if __name__=="__main__":
 p=argparse.ArgumentParser(); p.add_argument("video"); p.add_argument("--report",default="output/daily_news/rendered/technical_qa.json"); a=p.parse_args()
 r=run(Path(a.video)); Path(a.report).parent.mkdir(parents=True,exist_ok=True); Path(a.report).write_text(json.dumps(r,indent=2)); print(json.dumps(r,indent=2)); raise SystemExit(r["status"]!="PASS")
