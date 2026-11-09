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

    def test_archival_provenance_is_reported_and_authenticity_stays_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "archive.jpg"
            image.write_bytes(b"placeholder")
            image.with_suffix(".jpg.source.json").write_text(json.dumps({"provider": "Library of Congress", "page_url": "https://www.loc.gov/item/example/", "licence": "Public Domain", "creator": "Library of Congress"}), encoding="utf-8")
            report = build_visual_review([{"visual_type": "archival", "time_period": "1940s", "_media": {"type": "image", "path": str(image)}}], root, sample_video_frames=False)
            shot = report["shots"][0]
            self.assertTrue(shot["archival_material"])
            self.assertTrue(shot["archival_provenance_complete"])
            self.assertEqual(shot["archival_authenticity_status"], "pending_human_verification")
            self.assertEqual(report["archival_shot_count"], 1)
            self.assertFalse(report["historical_authenticity_verified"])

    def test_archival_provenance_gaps_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "archive.jpg"
            image.write_bytes(b"placeholder")
            image.with_suffix(".jpg.source.json").write_text(json.dumps({"provider": "Unknown archive"}), encoding="utf-8")
            report = build_visual_review([{"visual_type": "archival", "_media": {"type": "image", "path": str(image)}}], root, sample_video_frames=False)
            self.assertFalse(report["shots"][0]["archival_provenance_complete"])
            self.assertTrue(any("Archival provenance needs human completion" in issue["message"] for issue in report["issues"]))

    def test_missing_media_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_visual_review([{
                "_media": {"type": "video", "path": str(Path(tmp) / "missing.mp4")}
            }], tmp, sample_video_frames=False)
            self.assertTrue(any(issue["level"] == "error" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()


    def test_research_audit_detects_missing_and_unresolved_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "shot.jpg"
            image.write_bytes(b"placeholder")
            report = build_visual_review([
                {"source_refs": ["S1"], "chart_spec": {"values": [4]},
                 "_media": {"type": "image", "path": str(image)}},
                {"on_screen_text": "Claim without a citation",
                 "_media": {"type": "image", "path": str(image)}},
                {"source_refs": ["UNKNOWN"],
                 "_media": {"type": "image", "path": str(image)}},
            ], root, sample_video_frames=False,
               research_sources=[{"title": "Official report", "url": "https://example.org/report"}])
            audit = report["research_audit"]
            self.assertEqual(audit["research_source_count"], 1)
            self.assertEqual(audit["shots_with_resolved_sources"], ["shot_0001"])
            self.assertEqual(audit["evidence_graphics_missing_source_refs"], ["shot_0002"])
            self.assertEqual(audit["unresolved_source_refs"][0]["refs"], ["UNKNOWN"])
            self.assertFalse(audit["citation_coverage_complete"])
            self.assertTrue(audit["human_fact_check_required"])
