"""Fail-closed human decision application for shared review packages."""
import argparse,json
from pathlib import Path
from datetime import datetime
FIELDS=("editorial","factual","visual","audio","rights","disclosure")
QA=("semantic","audio","captions","assets")
def read(p): return json.loads(Path(p).read_text(encoding="utf-8"))
def write(p,x): Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def apply_decisions(package_dir,decisions_path):
 r=Path(package_dir).resolve(); m=read(r/"production_manifest.json"); d=read(decisions_path)
 if d.get("schema_version")!=1: raise ValueError("review decisions schema_version must be 1")
 reviewer=str(d.get("reviewer") or "").strip(); dt=str(d.get("reviewed_at") or "")
 if len(reviewer)<2: raise ValueError("named human reviewer required")
 try: datetime.fromisoformat(dt.replace("Z","+00:00"))
 except ValueError: raise ValueError("reviewed_at must be ISO-8601 with timezone")
 if any((d.get("confirmations") or {}).get(k)!="approved" for k in FIELDS): raise ValueError("all six human confirmations must be approved")
 scenes=m.get("scenes") or []; sr={str(x.get("scene_id")):x for x in d.get("scene_reviews",[]) if isinstance(x,dict)}
 if len(sr)!=len(scenes) or set(sr)!={str(s.get("scene_id")) for s in scenes}: raise ValueError("one scene review is required for every scene")
 for s in scenes:
  x=sr[str(s["scene_id"])]
  if x.get("status")!="approved" or len(str(x.get("notes") or "").strip())<20: raise ValueError(f"{s['scene_id']} needs approval and 20+ characters of inspection notes")
 ar={(str(x.get("scene_id")),str(x.get("path"))):x for x in d.get("asset_reviews",[]) if isinstance(x,dict)}
 expected={(str(s["scene_id"]),str(a["path"])) for s in scenes for a in s.get("assets",[])}
 if set(ar)!=expected or len(ar)!=len(expected): raise ValueError("one rights review is required for every manifest asset")
 for s in scenes:
  for a in s.get("assets",[]):
   x=ar[(str(s["scene_id"]),str(a["path"]))]; prov=a.get("provenance") or {}
   if x.get("status")!="reviewed" or len(str(x.get("notes") or "").strip())<20: raise ValueError(f"{s['scene_id']} asset needs rights approval and 20+ characters of notes")
   if not all(str(x.get(k) or "").strip() for k in ("license","license_url","attribution")): raise ValueError("every asset needs exact licence, licence URL and attribution")
   if prov.get("origin")=="unknown": raise ValueError("unknown provenance cannot be approved; replace asset or establish its origin")
   if prov.get("origin")=="source" and not prov.get("source_url"): raise ValueError("source asset missing original source URL")
   if prov.get("origin")=="ai_generated" and not prov.get("prompt"): raise ValueError("AI asset missing generation prompt")
   a["rights"]={"status":"reviewed","license":x["license"].strip(),"license_url":x["license_url"].strip(),"attribution":x["attribution"].strip(),"reviewer":reviewer,"reviewed_at":dt,"notes":x["notes"].strip()}
 qr=d.get("qa_reviews") or {}
 for n in QA:
  p=r/"qa"/(n+".json"); q=read(p); old=str(q.get("status") or "NOT_RUN").upper()
  if old=="FAIL": raise ValueError(f"qa/{n}.json FAIL cannot be overridden")
  if old not in {"PASS","REVIEW"}: raise ValueError(f"qa/{n}.json {old}: automated QA evidence missing")
  if old=="REVIEW":
   x=qr.get(n) or {}
   if x.get("status")!="PASS" or len(str(x.get("notes") or "").strip())<20: raise ValueError(f"qa/{n}.json requires human PASS and 20+ characters of notes")
   q["status"]="PASS";q["human_review"]={"reviewer":reviewer,"reviewed_at":dt,"notes":x["notes"].strip()};write(p,q)
 checklist={k:"approved" for k in FIELDS};checklist.update(human_review_status="approved",reviewer=reviewer,reviewed_at=dt)
 write(r/"review_checklist.json",checklist);(r/"review_checklist.md").write_text(f"# Human review completed\n\nReviewer: {reviewer}\nReviewed at: {dt}\n",encoding="utf-8");write(r/"production_manifest.json",m)
 return {"reviewer":reviewer,"reviewed_at":dt,"scene_count":len(scenes),"asset_count":len(expected)}
def main():
 p=argparse.ArgumentParser();p.add_argument("package_dir");p.add_argument("--decisions",required=True);a=p.parse_args();print(json.dumps(apply_decisions(a.package_dir,a.decisions),indent=2))
if __name__=="__main__":main()
