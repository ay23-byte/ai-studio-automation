import json
import tempfile
import unittest
from pathlib import Path

from scripts.import_wan_assets import import_wan_manifest


class WanAssetImportTests(unittest.TestCase):
    def test_imports_clips_as_unapproved_ai_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = root / "clips"
            clips.mkdir()
            (clips / "01_opening.mp4").write_bytes(b"x" * 60_000)
            manifest = root / "scene_manifest.json"
            manifest.write_text(json.dumps({"scenes": [{
                "id": "opening", "title": "Opening", "prompt": "Mountain sunrise",
                "file": "01_opening.mp4", "duration_seconds": 2.0,
                "width": 832, "height": 480
            }]}), encoding="utf-8")
            output = root / "imported"
            result = import_wan_manifest(manifest, clips, output)
            self.assertEqual(result["asset_count"], 1)
            asset = result["assets"][0]
            self.assertTrue(asset["ai_generated"])
            self.assertFalse(asset["human_visual_reviewed"])
            self.assertFalse(asset["licence_reviewed"])
            self.assertFalse(asset["disclosure_ready"])
            self.assertEqual(asset["review_status"], "pending_human_review")
            self.assertTrue(Path(asset["path"]).is_file())
            saved = json.loads((output / "wan_assets.json").read_text(encoding="utf-8"))
            self.assertFalse(saved["publication_ready"])

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = root / "clips"
            clips.mkdir()
            manifest = root / "scene_manifest.json"
            manifest.write_text(json.dumps({"scenes": [{
                "id": "bad", "prompt": "test", "file": "../outside.mp4"
            }]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_wan_manifest(manifest, clips, root / "out")

    def test_rejects_duplicate_scene_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = root / "clips"
            clips.mkdir()
            (clips / "a.mp4").write_bytes(b"x" * 60_000)
            manifest = root / "scene_manifest.json"
            row = {"id": "same", "prompt": "test", "file": "a.mp4"}
            manifest.write_text(json.dumps({"scenes": [row, row]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                import_wan_manifest(manifest, clips, root / "out")


if __name__ == "__main__":
    unittest.main()
