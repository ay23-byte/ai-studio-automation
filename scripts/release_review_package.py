"""Release an approved shared Shorts package as PRIVATE only."""
import argparse,hashlib,json
from pathlib import Path
from scripts.apply_review_decisions import apply_decisions
from scripts.validate_review_package import validate_package
def release_package(package_dir,decisions_path,expected_sha256,confirm_private_upload=False):
 if confirm_private_upload is not True: raise ValueError("explicit confirm_private_upload=true required")
 r=Path(package_dir).resolve();m=json.loads((r/"production_manifest.json").read_text())\n if m.get("mode") not in {"shortform","daily_news"}: raise ValueError("shared Short release only accepts shortform or daily_news packages")\n v=(r/str(m.get("video_file") or "video.mp4")).resolve()
 if r not in v.parents or not v.is_file(): raise ValueError("manifest video missing or escapes package")
 digest=hashlib.sha256(v.read_bytes()).hexdigest()
 if digest.lower()!=str(expected_sha256).lower(): raise ValueError("MP4 SHA-256 mismatch")
 apply_decisions(r,decisions_path);summary=validate_package(r)
 (r/"package_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
 if summary.get("status")!="APPROVED_FOR_UPLOAD" or summary.get("publication_eligible") is not True: raise ValueError("review package not approved: "+json.dumps(summary.get("required_human_reviews",[])))
 meta=json.loads((r/"youtube_metadata.json").read_text()) if (r/"youtube_metadata.json").is_file() else {}
 from core.uploader import upload
 title=str(meta.get("title") or m.get("topic") or "Engineering Physics Short");desc=str(meta.get("description") or m.get("topic") or "")
 marker="AI-STUDIO-REVIEWED-SHA256:"+digest
 vid=upload(v,title,desc,meta.get("tags") if isinstance(meta.get("tags"),list) else ["Engineering Physics","Shorts"],category_id=str(meta.get("category_id") or "27"),contains_synthetic=bool(meta.get("synthetic_media",True)),privacy_status="private",disable_scheduling=True,marker=marker,authorized=False)
 out={"video_id":vid,"url":"https://www.youtube.com/watch?v="+vid,"privacy":"private","scheduled":False,"public_publish_performed":False,"mode":m.get("mode"),"topic":m.get("topic"),"video_sha256":digest}
 (r/"youtube-upload-result.json").write_text(json.dumps(out,indent=2)+"\n");return out
def main():
 p=argparse.ArgumentParser();p.add_argument("--package-dir",required=True);p.add_argument("--decisions",required=True);p.add_argument("--video-sha256",required=True);p.add_argument("--confirm-private-upload",action="store_true");a=p.parse_args();print(json.dumps(release_package(a.package_dir,a.decisions,a.video_sha256,a.confirm_private_upload),indent=2))
if __name__=="__main__":main()
