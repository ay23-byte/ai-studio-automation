import json
import tempfile
import unittest
from pathlib import Path

from core.visual_production import build_visual_review


class VisualProductionTests(unittest.TestCase):
    def test_review_report_marks_human_review_and_preserves_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "shot.jpg"
            image.write_bytes(b"placeholder")
            report = build_visual_review([{
                "_start": 0.0, "_dur": 4.0, "narration": "The company arrived in Bengal.",
                "visual_prompt": "East India Company ship approaching Bengal",
                "_media": {"type": "image", "path": str(image)}
            }], root, sample_video_frames=False)
            self.assertEqual(report["shot_count"], 1)
            self.assertFalse(report["publication_ready"])
            self.assertFalse(report["shots"][0]["semantic_match_verified"])
            self.assertTrue(report["shots"][0]["human_visual_review_required"])
            saved = json.loads((root / "visual_review_report.json").read_text())
            self.assertIn("East India Company", saved["shots"][0]["visual_prompt"])

    def test_missing_media_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_visual_review([{
                "_media": {"type": "video", "path": str(Path(tmp) / "missing.mp4")}
            }], tmp, sample_video_frames=False)
            self.assertTrue(any(issue["level"] == "error" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
