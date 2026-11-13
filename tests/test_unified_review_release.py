import json,tempfile,unittest
from pathlib import Path
from scripts.apply_review_decisions import apply_decisions
from scripts.release_review_package import release_package
class HumanDecisionTests(unittest.TestCase):
 def setup_package(self,r):
  (r/"qa").mkdir();(r/"assets").mkdir();(r/"preview"/"frames").mkdir(parents=True);(r/"video.mp4").write_bytes(b"video");(r/"captions.srt").write_text("caption");(r/"assets/a.jpg").write_bytes(b"img")
  m={"schema_version":1,"mode":"shortform","topic":"T","video_file":"video.mp4","captions_file":"captions.srt","scenes":[{"scene_id":"s1","narration":"voice","assets":[{"path":"assets/a.jpg","asset_type":"image","provenance":{"origin":"source","source_url":"https://example.org/a"},"rights":{"status":"unknown"}}]}]};(r/"production_manifest.json").write_text(json.dumps(m))
  for n in ("technical","semantic","audio","captions","assets"):(r/"qa"/(n+".json")).write_text(json.dumps({"status":"PASS" if n=="technical" else "REVIEW"}))
  d={"schema_version":1,"reviewer":"Reviewer A","reviewed_at":"2026-11-13T12:00:00Z","confirmations":{x:"approved" for x in ("editorial","factual","visual","audio","rights","disclosure")},"scene_reviews":[{"scene_id":"s1","status":"approved","notes":"Inspected the complete shot against its narration."}],"asset_reviews":[{"scene_id":"s1","path":"assets/a.jpg","status":"reviewed","license":"CC BY 4.0","license_url":"https://creativecommons.org/licenses/by/4.0/","attribution":"Creator and source title","notes":"Checked original item page and exact licence conditions."}],"qa_reviews":{x:{"status":"PASS","notes":"Reviewed report and corresponding media evidence."} for x in ("semantic","audio","captions","assets")}}
  (r/"decisions.json").write_text(json.dumps(d));return d
 def test_applies_human_reviews_and_rights(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);self.setup_package(r);result=apply_decisions(r,r/"decisions.json");self.assertEqual(result["scene_count"],1);self.assertEqual(json.loads((r/"review_checklist.json").read_text())["human_review_status"],"approved")
 def test_unknown_provenance_blocks(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);m=self.setup_package(r);m["scenes"][0]["assets"][0]["provenance"]={"origin":"unknown"};(r/"production_manifest.json").write_text(json.dumps(m))
   with self.assertRaisesRegex(ValueError,"unknown provenance"):apply_decisions(r,r/"decisions.json")
 def test_failed_qa_never_overridden(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);self.setup_package(r);(r/"qa/semantic.json").write_text('{"status":"FAIL"}')
   with self.assertRaisesRegex(ValueError,"cannot be overridden"):apply_decisions(r,r/"decisions.json")
 def test_requires_explicit_private_upload(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);self.setup_package(r)
   with self.assertRaisesRegex(ValueError,"confirm_private_upload"):release_package(r,r/"decisions.json","0"*64)
if __name__=="__main__":unittest.main()
