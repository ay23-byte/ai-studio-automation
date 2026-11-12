"""Free animated chart/timeline/evidence cards for documentary scenes."""
from __future__ import annotations
import math, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W,H=1920,1080
BG=(10,15,24); PANEL=(21,29,40); WHITE=(238,236,229); MUTED=(157,168,180)
RED=(171,133,74); YELLOW=(218,184,115); GRID=(54,65,79)

def _font(size,bold=False,text=""):
    """Script-aware font (Devanagari chapter titles/slates no longer render as boxes)."""
    from core.fonts import load_font
    return load_font(size,bold,text)

def _base(title,kicker):
    im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,12),fill=RED); d.text((110,72),kicker.upper(),font=_font(25,True),fill=YELLOW)
    d.text((110,125),str(title)[:90],font=_font(54,True,title),fill=WHITE)
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
        if d.textbbox((0,0),test,font=_font(43,True,text))[2]>1500 and line: lines.append(line); line=word
        else: line=test
    if line: lines.append(line)
    y=360
    for i,s in enumerate(lines[:max(1,math.ceil(len(lines)*p))]):
        d.rounded_rectangle((120,y-18,1800,y+64),radius=12,fill=PANEL,outline=GRID,width=2)
        d.text((155,y),s,font=_font(43,True,s),fill=YELLOW if i==0 else WHITE); y+=112
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


def render_context_graphic(shot, duration, build_dir, scene_id):
    """Render a disclosed contextual graphic when genuine archival media is unavailable.

    This is an editorial context card, never an archival substitute or a claim of
    historical authenticity. All visible facts must come from the supplied beat.
    """
    from textwrap import wrap
    root = Path(build_dir) / "context_graphics"
    root.mkdir(parents=True, exist_ok=True)
    subject = str(shot.get("subject") or shot.get("title") or "Historical context")[:100]
    location = str(shot.get("location") or "Location not specified")[:120]
    period = str(shot.get("time_period") or "Date not specified")[:100]
    description = str(shot.get("visual_guardrail") or shot.get("narration") or "")[:420]
    im, d = _base(subject, "CONTEXT GRAPHIC · NOT ARCHIVAL FOOTAGE")
    d.text((115, 310), "SCENE CONTEXT", font=_font(25, True), fill=YELLOW)
    d.text((115, 375), location, font=_font(42, True, location), fill=WHITE)
    d.text((115, 445), period, font=_font(30), fill=MUTED)
    y = 565
    for line in wrap(description, width=72)[:4]:
        d.text((115, y), line, font=_font(27, False, line), fill=WHITE)
        y += 52
    d.rounded_rectangle((110, 875, 1810, 965), radius=12, fill=PANEL, outline=YELLOW, width=2)
    d.text((145, 900), "ILLUSTRATIVE CONTEXT ONLY  •  NO ARCHIVAL IMAGE AVAILABLE",
           font=_font(24, True), fill=YELLOW)
    out = root / f"{scene_id}.png"
    im.save(out, optimize=True)
    return out if out.is_file() and out.stat().st_size > 1000 else None


def _slate_brand():
    """Footer line of a chapter slate: the configured channel, not a fixed genre label."""
    try:
        from core import config as cfg
        name = str(getattr(cfg, "CHANNEL_NAME", "") or "").strip()
    except Exception:
        name = ""
    return name.upper()[:48] if name and name.isascii() else "DOCUMENTARY"


def render_chapter_title(title, chapter_number, duration, build_dir):
    """Create a restrained animated chapter slate with no generated factual claims."""
    root=Path(build_dir); root.mkdir(parents=True,exist_ok=True)
    fps=15; count=max(30,min(90,int(max(float(duration),2)*fps)))
    safe_title=str(title or f"Chapter {chapter_number}")[:100]
    for i in range(count):
        p=(i+1)/count
        im,d=_base(safe_title,f"CHAPTER {int(chapter_number):02d}")
        # Reveal a thin editorial progress line across the lower third.
        x_end=int(110+(W-220)*p)
        d.rounded_rectangle((110,850,x_end,862),radius=6,fill=YELLOW)
        d.text((112,900),_slate_brand(),font=_font(22,True),fill=MUTED)
        im.save(root/f"title_{int(chapter_number):02d}_{i:04d}.png",optimize=True)
    out=root/f"chapter_title_{int(chapter_number):02d}.mp4"
    from core.ffmpeg_utils import ffmpeg_bin
    cmd=[ffmpeg_bin(),"-y","-loglevel","error","-framerate",str(fps),"-i",
         str(root/f"title_{int(chapter_number):02d}_%04d.png"),"-t",f"{float(duration):.3f}",
         "-vf","fps=30,format=yuv420p","-c:v","libx264","-preset","veryfast","-crf","20",
         "-movflags","+faststart",str(out)]
    try: subprocess.run(cmd,check=True,timeout=max(60,int(duration*15)))
    except (OSError,subprocess.SubprocessError): return None
    if not (out.is_file() and out.stat().st_size>1000): return None
    # The slate is drawn by this project from the chapter heading alone.  Without
    # this record the rights register listed every slate as "Unknown source".
    import json
    out.with_suffix(out.suffix+".source.json").write_text(json.dumps({
        "provider":"LabLens/AI Studio local graphic renderer","asset_type":"programmatic_graphic",
        "ai_generated":False,"graphic_kind":"chapter_title","licence":"Project-created original graphic",
        "font_licence":"SIL Open Font License 1.1 (Noto Sans / Noto Sans Devanagari)",
        "font_licence_url":"https://openfontlicense.org",
        "font_attribution":"Typeface: Noto Sans, © The Noto Project Authors, SIL OFL 1.1.",
        "attribution_required":False,"source_text":safe_title,
    },ensure_ascii=False,indent=2),encoding="utf-8")
    return out
