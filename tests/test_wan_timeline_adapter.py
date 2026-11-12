import json
import tempfile
import unittest
from pathlib import Path

from core.wan_timeline_adapter import apply_mapped_wan_review


class WanTimelineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clip = self.root / "wan_scene.mp4"
        self.clip.write_bytes(b"fixture mp4 placeholder")
        self.report = self.root / "mapped_review.json"
        self.timeline = [{
            "narration": "The factory changed the town.",
            "_chapter_index": 0,
            "_media": {"type": "image", "path": str(self.root / "old.jpg")},
            "visual_type": "stock",
        }]
        self.payload = {
            "wan_asset_mapping": {
                "human_review_required": True,
                "publication_ready": False,
                "mappings": [{"scene_id": "factory", "shot_id": "shot_0001"}],
            },
            "shots": [{
                "shot_id": "shot_0001",
                "chapter_id": "0",
                "narration": "The factory changed the town.",
                "media_path": str(self.clip),
                "wan_scene_id": "factory",
                "wan_generation_prompt": "A period-accurate factory exterior.",
                "source": {
                    "provider": "Wan2.1 AI video generation",
                    "model": "Wan2.1",
                    "ai_generated": True,
                    "licence_status": "not_reviewed",
                },
            }],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def write_report(self):
        self.report.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_applies_wan_clip_and_preserves_review_gates(self):
        self.write_report()
        result = apply_mapped_wan_review(self.timeline, self.report)
        shot = self.timeline[0]
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(shot["_media"], {"type": "video", "path": str(self.clip.resolve())})
        self.assertEqual(shot["visual_type"], "ai_reconstruction")
        self.assertTrue(shot["ai_generated"])
        self.assertEqual(shot["wan_scene_id"], "factory")
        self.assertTrue(shot["human_visual_review_required"])
        self.assertFalse(result["publication_ready"])

    def test_rejects_changed_narration_identity(self):
        self.payload["shots"][0]["narration"] = "A different narration beat."
        self.write_report()
        with self.assertRaisesRegex(ValueError, "narration"):
            apply_mapped_wan_review(self.timeline, self.report)

    def test_rejects_missing_clip(self):
        self.payload["shots"][0]["media_path"] = str(self.root / "missing.mp4")
        self.write_report()
        with self.assertRaisesRegex(ValueError, "missing or is not an MP4"):
            apply_mapped_wan_review(self.timeline, self.report)

    def test_rejects_missing_ai_provenance(self):
        self.payload["shots"][0]["source"]["ai_generated"] = False
        self.write_report()
        with self.assertRaisesRegex(ValueError, "AI provenance"):
            apply_mapped_wan_review(self.timeline, self.report)


if __name__ == "__main__":
    unittest.main()
