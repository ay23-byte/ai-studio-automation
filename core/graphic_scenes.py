"""Free animated chart/timeline/evidence cards for documentary scenes."""
from __future__ import annotations
import math, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W,H=1920,1080
BG=(12,14,19); PANEL=(25,28,36); WHITE=(242,242,242); MUTED=(160,166,177)
RED=(176,18,27); YELLOW=(255,230,0); GRID=(58,62,72)

def _font(size,bold=False):
    names=(["C:/Windows/Fonts/arialbd.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
           ["C:/Windows/Fonts/arial.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for name in names:
        try: return ImageFont.truetype(name,size)
        except OSError: pass
    return ImageFont.load_default()

def _base(title,kicker):
    im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,12),fill=RED); d.text((110,72),kicker.upper(),font=_font(25,True),fill=YELLOW)
    d.text((110,125),str(title)[:90],font=_font(54,True),fill=WHITE)
    d.line((110,220,W-110,220),fill=GRID,width=2)
    return im,d

def _chart(spec,p):
    labels,values=spec.get("labels") or [],spec.get("values") or []
    if not isinstance(labels,list) or not isinstance(values,list) or not values or len(labels)!=len(values): return None
    try: values=[float(v) for v in values]
    except (TypeError,ValueError): return None
    if not all(math.isfinite(v) for v in values): return None
    im,d=_base(spec.get("title") or "Reported figures",spec.get("unit") or "SOURCE DATA")
    l,t,r,b=190,330,1700,850; lo=min(0,min(values)); hi=max(values); hi=hi if hi!=lo else lo+1
    for k in range(5):
        y=b-k*(b-t)/4; d.line((l,y,r,y),fill=GRID,width=2)
        d.text((105,int(y-15)),f"{lo+k*(hi-lo)/4:,.2f}",font=_font(20),fill=MUTED)
    slot=(r-l)/len(values)
    for i,(label,val) in enumerate(zip(labels,values)):
        x0=l+i*slot+slot*.22; x1=l+(i+1)*slot-slot*.22
        target=b-(val-lo)/(hi-lo)*(b-t); reveal=max(0,min(1,p*len(values)-i)); y=b-(b-target)*reveal
        d.rounded_rectangle((int(x0),int(y),int(x1),b),radius=10,fill=RED if i%2==0 else YELLOW)
        if reveal>.75: d.text((int(x0),int(max(t-38,y-42))),f"{val:,.2f}",font=_font(23,True),fill=WHITE)
        d.text((int(x0),b+22),str(label)[:18],font=_font(19),fill=MUTED)
    return im

def _timeline(events,p,title):
    if not isinstance(events,list) or not events: return None
    im,d=_base(title or "Timeline","CHRONOLOGY"); y=565
    d.line((170,y,1750,y),fill=YELLOW,width=5)
    for i,e in enumerate(events):
        if not isinstance(e,dict): continue
        x=190+1500*i/max(len(events)-1,1); active=p>=(i+1)/len(events); c=RED if active else MUTED
        d.ellipse((int(x-15),y-15,int(x+15),y+15),fill=c,outline=WHITE,width=2)
        above=i%2==0; sy=y-130 if above else y+130; d.line((x,y,x,sy),fill=c,width=3)
        date=str(e.get("date") or e.get("year") or f"Event {i+1}")[:24]
        label=str(e.get("label") or e.get("event") or e.get("title") or "")[:58]
        d.text((int(x-115),sy-54 if above else sy+12),date,font=_font(25,True),fill=YELLOW if active else WHITE)
        d.text((int(x-115),sy-18 if above else sy+48),label,font=_font(20),fill=WHITE if active else MUTED)
    return im

def _evidence(text,p,title):
    if not text: return None
    im,d=_base(title or "Key evidence","ON-SCREEN EVIDENCE")
    words=str(text).split(); lines=[]; line=""
    for word in words:
        test=(line+" "+word).strip()
        if d.textbbox((0,0),test,font=_font(43,True))[2]>1500 and line: lines.append(line); line=word
        else: line=test
    if line: lines.append(line)
    y=360
    for i,s in enumerate(lines[:max(1,math.ceil(len(lines)*p))]):
        d.rounded_rectangle((120,y-18,1800,y+64),radius=12,fill=PANEL,outline=GRID,width=2)
        d.text((155,y),s,font=_font(43,True),fill=YELLOW if i==0 else WHITE); y+=112
    return im

def render_graphic_scene(shot,duration,build_dir,scene_id):
    kind=str(shot.get("visual_type") or "").lower()
    chart=shot.get("chart_spec"); events=shot.get("timeline_events") or []; text=shot.get("on_screen_text") or ""
    if kind not in {"chart","timeline","document","evidence","callout","infographic","diagram","map"}: return None
    if not (isinstance(chart,dict) or events or text): return None
    root=Path(build_dir)/"graphic_frames"/scene_id; root.mkdir(parents=True,exist_ok=True)
    fps=15; count=max(30,min(180,int(max(float(duration),2)*fps)))
    for i in range(count):
        p=(i+1)/count; frame=_chart(chart,p) if isinstance(chart,dict) else None
        if frame is None and events: frame=_timeline(events,p,shot.get("title",""))
        if frame is None and text: frame=_evidence(text,p,shot.get("title",""))
        if frame is None: return None
        frame.save(root/f"frame_{i:04d}.png",optimize=True)
    out=Path(build_dir)/(scene_id+".mp4")
    cmd=["ffmpeg","-y","-loglevel","error","-framerate",str(fps),"-i",str(root/"frame_%04d.png"),
         "-t",f"{float(duration):.3f}","-vf","fps=30,format=yuv420p","-c:v","libx264",
         "-preset","veryfast","-crf","20","-movflags","+faststart",str(out)]
    try: subprocess.run(cmd,check=True,timeout=max(90,int(duration*20)))
    except (OSError,subprocess.SubprocessError): return None
    return out if out.is_file() and out.stat().st_size>1000 else None
