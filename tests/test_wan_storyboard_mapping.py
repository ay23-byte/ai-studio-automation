import json
import tempfile
import unittest
from pathlib import Path

from scripts.map_wan_assets_to_storyboard import map_wan_assets_to_storyboard
from core.editorial_review import load_reviews, save_review


class WanStoryboardMappingTests(unittest.TestCase):
    def _fixture(self, root):
        media = root / "wan" / "01_opening.mp4"
        media.parent.mkdir()
        media.write_bytes(b"valid-enough-test-fixture")
        assets = root / "wan" / "wan_assets.json"
        assets.write_text(json.dumps({"assets": [{
            "scene_id": "opening", "asset_id": "wan_opening",
            "path": str(media), "prompt": "Aerial mountain sunrise"
        }]}), encoding="utf-8")
        report = root / "visual_review_report.json"
        report.write_text(json.dumps({"shots": [{
            "shot_id": "shot_0001", "chapter_id": "1",
            "media_path": "old.mp4", "media_type": "video",
            "visual_prompt": "Opening mountains", "narration": "The story begins."
        }]}), encoding="utf-8")
        mapping = root / "mapping.json"
        mapping.write_text(json.dumps({"mappings": [{
            "scene_id": "opening", "shot_id": "shot_0001"
        }]}), encoding="utf-8")
        return assets, report, mapping, media

    def test_maps_clip_and_keeps_all_review_gates_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets, report, mapping, media = self._fixture(root)
            output = root / "mapped" / "visual_review_report.json"
            result = map_wan_assets_to_storyboard(assets, report, mapping, output)
            shot = result["shots"][0]
            self.assertEqual(Path(shot["media_path"]), media.resolve())
            self.assertEqual(shot["visual_type"], "ai_reconstruction")
            self.assertTrue(shot["ai_generated"])
            self.assertFalse(result["publication_ready"])
            store = load_reviews(output, result["shots"])
            decision = store["shots"]["shot_0001"]
            self.assertEqual(decision["status"], "pending")
            self.assertEqual(decision["source_review_status"], "pending")
            self.assertEqual(decision["rights_review_status"], "pending")
            self.assertEqual(json.loads(report.read_text())["shots"][0]["media_path"], "old.mp4")

    def test_changed_asset_invalidates_previous_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets, report, mapping, _ = self._fixture(root)
            output = root / "mapped" / "visual_review_report.json"
            first = map_wan_assets_to_storyboard(assets, report, mapping, output)
            save_review(output, "shot_0001", status="approved",
                        source_review_status="reviewed", rights_review_status="reviewed",
                        rights_notes="Reviewed by editor", licence_name="AI-generated")
            changed = json.loads(assets.read_text())
            changed["assets"][0]["prompt"] = "Different generated scene"
            assets.write_text(json.dumps(changed), encoding="utf-8")
            second = map_wan_assets_to_storyboard(assets, report, mapping, output)
            decision = load_reviews(output, second["shots"])["shots"]["shot_0001"]
            self.assertEqual(decision["status"], "pending")
            self.assertEqual(decision["source_review_status"], "pending")
            self.assertEqual(decision["rights_review_status"], "pending")

    def test_rejects_unknown_scene_and_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets, report, mapping, _ = self._fixture(root)
            mapping.write_text(json.dumps({"mappings": [{
                "scene_id": "missing", "shot_id": "shot_0001"
            }]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown Wan scene_id"):
                map_wan_assets_to_storyboard(assets, report, mapping, root / "out.json")

    def test_rejects_duplicate_target_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets, report, mapping, _ = self._fixture(root)
            payload = json.loads(mapping.read_text())
            payload["mappings"].append({"scene_id": "opening", "shot_id": "shot_0001"})
            mapping.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only once"):
                map_wan_assets_to_storyboard(assets, report, mapping, root / "out.json")


if __name__ == "__main__":
    unittest.main()
