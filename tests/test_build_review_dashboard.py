import json,tempfile,unittest
from pathlib import Path
from scripts.build_review_dashboard import build_dashboard
class ReviewDashboardTests(unittest.TestCase):
 def test_creates_offline_dashboard_and_complete_template(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);(r/"production_manifest.json").write_text(json.dumps({"schema_version":1,"mode":"shortform","topic":"Test","video_file":"video.mp4","captions_file":"captions.srt","scenes":[{"scene_id":"s1","narration":"Test narration","assets":[{"path":"assets/a.jpg","asset_type":"image","provenance":{"origin":"source","source_url":"https://example.org"}}]}]}))
   (r/"qa").mkdir();(r/"qa/technical.json").write_text('{"status":"PASS"}')
   page=build_dashboard(r);self.assertTrue(page.is_file());self.assertIn("video.mp4",page.read_text())
   t=json.loads((r/"review_decisions_template.json").read_text());self.assertEqual(t["scene_reviews"][0]["scene_id"],"s1");self.assertEqual(t["asset_reviews"][0]["path"],"assets/a.jpg");self.assertEqual(set(t["confirmations"]),{"editorial","factual","visual","audio","rights","disclosure"})
if __name__=="__main__":unittest.main()
