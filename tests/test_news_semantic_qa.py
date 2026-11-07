import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.news_semantic_qa import run_semantic_qa


class SemanticQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.video = root / "review_short.mp4"
        self.video.write_bytes(b"video-placeholder")
        self.manifest = root / "production_manifest.json"
        self.output = root / "advanced_qa"
        self.manifest.write_text(json.dumps({"shots": [
            {"shot_id": "shot_01", "start_seconds": 0, "duration_seconds": 5,
             "narration": "A cargo ship enters a port.",
             "visual_prompt": "Cargo ship approaching a port.", "visual_type": "video"},
            {"shot_id": "shot_02", "start_seconds": 5.25, "duration_seconds": 4,
             "narration": "Workers unload containers.", "visual_prompt": "Port workers unloading containers.",
             "visual_type": "image"},
        ]}), encoding="utf-8")

    @patch("core.news_semantic_qa.verify_shot_semantics")
    @patch("core.news_semantic_qa._sample_midpoint", side_effect=["frame1.jpg", "frame2.jpg"])
    def test_all_shots_matching_passes_but_requires_human_review(self, sample, verify):
        verify.side_effect = [
            {"status": "match", "match_score": 0.9, "human_review_required": True},
            {"status": "match", "match_score": 0.88, "human_review_required": True},
        ]
        result = run_semantic_qa(self.video, self.manifest, self.output)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["semantic_match_verified"])
        self.assertTrue(result["human_review_required"])
        self.assertEqual(sample.call_count, 2)
        self.assertEqual(verify.call_count, 2)
        saved = json.loads((self.output / "semantic_qa_report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["shot_count"], 2)

    @patch("core.news_semantic_qa.verify_shot_semantics", return_value={"status": "mismatch"})
    @patch("core.news_semantic_qa._sample_midpoint", return_value="frame.jpg")
    def test_any_mismatch_fails(self, sample, verify):
        result = run_semantic_qa(self.video, self.manifest, self.output)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["mismatch_count"], 2)

    @patch("core.news_semantic_qa.verify_shot_semantics", return_value={"status": "unavailable"})
    @patch("core.news_semantic_qa._sample_midpoint", return_value="frame.jpg")
    def test_unavailable_model_requires_review_not_pass(self, sample, verify):
        result = run_semantic_qa(self.video, self.manifest, self.output)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["unavailable_count"], 2)

    def test_missing_shot_manifest_is_unavailable(self):
        self.manifest.write_text(json.dumps({"shots": []}), encoding="utf-8")
        result = run_semantic_qa(self.video, self.manifest, self.output)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertTrue(result["human_review_required"])


if __name__ == "__main__":
    unittest.main()
