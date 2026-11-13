"""Create an offline, self-contained visual review page and decision template."""
import argparse,html,json
from pathlib import Path
FIELDS=("editorial","factual","visual","audio","rights","disclosure")
QA=("semantic","audio","captions","assets")
def build_dashboard(package_dir):
 r=Path(package_dir).resolve();m=json.loads((r/"production_manifest.json").read_text(encoding="utf-8"))
 scenes=m.get("scenes") or []
 template={"schema_version":1,"reviewer":"","reviewed_at":"","confirmations":{k:"pending" for k in FIELDS},
 "scene_reviews":[{"scene_id":s.get("scene_id"),"status":"pending","notes":""} for s in scenes],
 "asset_reviews":[{"scene_id":s.get("scene_id"),"path":a.get("path"),"status":"pending","license":"","license_url":"","attribution":"","notes":""} for s in scenes for a in s.get("assets",[])],
 "qa_reviews":{k:{"status":"pending","notes":""} for k in QA}}
 (r/"review_decisions_template.json").write_text(json.dumps(template,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 cards=[]
 for s in scenes:
  assets=[]
  for a in s.get("assets",[]):
   p=html.escape(str(a.get("path") or ""),quote=True); prov=a.get("provenance") or {};rights=a.get("rights") or {}
   kind=str(a.get("asset_type") or "")
   media=f'<video controls preload="metadata" src="{p}"></video>' if kind=="video" else f'<img loading="lazy" src="{p}" alt="Asset preview">'
   assets.append(f'<div class="asset">{media}<p><b>{html.escape(kind)}</b> · origin: {html.escape(str(prov.get("origin") or "unknown"))} · provider: {html.escape(str(prov.get("provider") or "not recorded"))}</p><p>Source: {html.escape(str(prov.get("source_url") or "not recorded"))}</p><p>Current licence: {html.escape(str(rights.get("license") or "not reviewed"))}</p><p>File: <code>{p}</code></p></div>')
  cards.append(f'<section><h2>{html.escape(str(s.get("scene_id") or "Scene"))}</h2><p>{html.escape(str(s.get("narration") or ""))}</p>{"".join(assets)}</section>')
 qa_rows=[]
 for name in ("technical","semantic","audio","captions","assets"):
  p=r/"qa"/(name+".json");status="NOT_RUN"
  if p.is_file():
   try:status=json.loads(p.read_text()).get("status","NOT_RUN")
   except Exception:status="INVALID"
  qa_rows.append(f"<li><b>{name}</b>: {html.escape(str(status))}</li>")
 page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Studio human review</title>
<style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#111827;color:#f3f4f6}section,.panel{background:#1f2937;padding:1rem;margin:1rem 0;border-radius:12px}video{width:100%;max-height:65vh;background:#000}img{max-width:100%;max-height:280px;object-fit:contain}.asset{border-top:1px solid #4b5563;padding:.8rem 0}a,button{color:#fff;background:#2563eb;padding:.7rem 1rem;border:0;border-radius:8px;display:inline-block}code{overflow-wrap:anywhere}li{margin:.5rem}</style>
<body><h1>Human review package</h1><p><b>Mode:</b> MODE · <b>Topic:</b> TOPIC</p><div class="panel"><h2>Watch the complete video</h2><video controls preload="metadata" src="video.mp4"></video><p>Captions file: <a href="CAPTIONS">Open captions</a></p><p>SHA-256 must be calculated for this exact video.mp4 before release.</p></div>
<div class="panel"><h2>Automated QA reports</h2><ul>{{QA_ROWS}}</ul><p>Automated results are evidence, not human approval. A FAIL cannot be overridden.</p></div><h2>Scene-by-scene visual and provenance inspection</h2>{{SCENE_CARDS}}
<div class="panel"><h2>Complete your review</h2><ol><li>Watch the full MP4 and inspect every scene and source page.</li><li>Fill the decision template: reviewer, timestamp, all six confirmations, every scene note, every asset licence/terms URL/attribution, and QA notes.</li><li>Do not approve unknown provenance or missing rights. Replace such assets before release.</li><li>Only QA reports marked REVIEW may be resolved by a human note; FAIL and NOT_RUN remain blocked.</li><li>Use the separate private-release workflow only after you are satisfied with this exact video.</li></ol><a href="review_decisions_template.json" download>Download decision JSON template</a> <a href="production_manifest.json">Open production manifest</a> <a href="package_summary.json">Open package validation</a></div>
</body></html>'''
 page=page.replace("MODE",html.escape(str(m.get("mode")))).replace("TOPIC",html.escape(str(m.get("topic")))).replace("CAPTIONS",html.escape(str(m.get("captions_file") or "captions.srt"))).replace("{{QA_ROWS}}","".join(qa_rows)).replace("{{SCENE_CARDS}}","".join(cards))
 (r/"review_dashboard.html").write_text(page,encoding="utf-8")
 return r/"review_dashboard.html"
def main():
 p=argparse.ArgumentParser();p.add_argument("package_dir");a=p.parse_args();print(build_dashboard(a.package_dir))
if __name__=="__main__":main()
