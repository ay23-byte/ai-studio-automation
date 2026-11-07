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

    def test_report_preserves_evidence_and_graphic_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "shot.jpg"
            image.write_bytes(b"placeholder")
            chart = {"chart_title": "Revenue by year", "unit": "USD", "labels": ["2022"], "values": [12], "source_refs": ["S1"]}
            report = build_visual_review([{
                "_start": 2.0, "_dur": 5.0, "narration": "Revenue rose.",
                "visual_prompt": "A sourced revenue chart",
                "source_refs": ["S1"], "chart_spec": chart,
                "timeline_events": [{"date": "2022", "event": "Launch", "source_refs": ["S1"]}],
                "on_screen_text": "2022 • $12",
                "sound_design": "Subtle paper movement",
                "_media": {"type": "image", "path": str(image)}
            }], root, sample_video_frames=False)
            shot = report["shots"][0]
            self.assertEqual(shot["source_refs"], ["S1"])
            self.assertEqual(shot["chart_spec"], chart)
            self.assertEqual(shot["timeline_events"][0]["event"], "Launch")
            self.assertEqual(shot["on_screen_text"], "2022 • $12")
            self.assertEqual(shot["sound_design"], "Subtle paper movement")

    def test_missing_media_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_visual_review([{
                "_media": {"type": "video", "path": str(Path(tmp) / "missing.mp4")}
            }], tmp, sample_video_frames=False)
            self.assertTrue(any(issue["level"] == "error" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
