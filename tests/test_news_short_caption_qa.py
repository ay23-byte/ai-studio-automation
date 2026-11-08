import json
import tempfile
import unittest
from pathlib import Path

from core.news_short_caption_qa import audit_captions
from core.news_short_production import _write_ass


class CaptionQATests(unittest.TestCase):
    def test_scene_caption_text_and_timing_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            narration = "यह हिंदी में बोली गई पंक्ति है।"
            manifest = {"captions": str(root / "captions.ass"), "shots": [
                {"start_seconds": 0.0, "duration_seconds": 4.0, "narration": narration}
            ]}
            (root / "production_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            _write_ass([{"say": narration}], [4.0], root / "captions.ass")
            result = audit_captions(root / "production_manifest.json")
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(result["caption_text_and_timing_verified"])

    def test_caption_must_match_scene_narration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"captions": str(root / "captions.ass"), "shots": [
                {"start_seconds": 0.0, "duration_seconds": 4.0, "narration": "Correct spoken line"}
            ]}
            (root / "production_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            _write_ass([{"say": "Different caption line"}], [4.0], root / "captions.ass")
            result = audit_captions(root / "production_manifest.json")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("does not match" in error for error in result["errors"]))

    def test_caption_timing_must_match_scene_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {"captions": str(root / "captions.ass"), "shots": [
                {"start_seconds": 1.0, "duration_seconds": 4.0, "narration": "Line"}
            ]}
            (root / "production_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            _write_ass([{"say": "Line"}], [4.0], root / "captions.ass")
            result = audit_captions(root / "production_manifest.json")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("starts" in error for error in result["errors"]))

    def test_missing_caption_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "production_manifest.json").write_text(json.dumps({"shots": [
                {"start_seconds": 0, "duration_seconds": 2, "narration": "Line"}
            ]}), encoding="utf-8")
            result = audit_captions(root / "production_manifest.json")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(result["human_review_required"])


if __name__ == "__main__":
    unittest.main()
