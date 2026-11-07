"""Upload a reviewed Short as PRIVATE only; public publishing is deliberately unsupported."""
import argparse,json
from pathlib import Path

def main():
 p=argparse.ArgumentParser(); p.add_argument("metadata"); p.add_argument("approval"); p.add_argument("--video",default="output/daily_news/rendered/review_short.mp4"); p.add_argument("--qa",default="output/daily_news/rendered/technical_qa.json"); a=p.parse_args()
 approval=json.loads(Path(a.approval).read_text()); qa=json.loads(Path(a.qa).read_text()); meta=json.loads(Path(a.metadata).read_text())
 if approval.get("status")!="approved_private_upload": raise ValueError("Separate human approval status=approved_private_upload is required.")
 if qa.get("status")!="PASS": raise ValueError("Technical QA must PASS before upload.")
 video=Path(a.video)
 if not video.is_file(): raise FileNotFoundError(video)
 if not all(k in meta for k in ("title","description","tags")): raise ValueError("Metadata requires title, description and tags.")
 from core.uploader import upload
 vid=upload(video,meta["title"],meta["description"],meta["tags"],category_id=str(meta.get("category_id","25")),contains_synthetic=bool(meta.get("synthetic_media",True)),privacy_status="private")
 print(json.dumps({"video_id":vid,"url":"https://www.youtube.com/watch?v="+vid,"privacy":"private","public_publish_performed":False},indent=2))
if __name__=="__main__": main()
