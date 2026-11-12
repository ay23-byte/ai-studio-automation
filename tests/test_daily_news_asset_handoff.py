import json
import tempfile
import unittest
from pathlib import Path

from scripts.attach_daily_news_assets import attach_assets


class DailyNewsAssetHandoffTests(unittest.TestCase):
    def test_attaches_exact_scene_assets_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact"
            (artifact / "assets").mkdir(parents=True)
            scenes = []
            inventory = []
            approved_scenes = []
            for index in range(1, 5):
                asset = artifact / "assets" / f"scene_{index:02d}.jpg"
                asset.write_bytes(b"fake-image")
                asset.with_suffix(".jpg.source.json").write_text(json.dumps({
                    "provider": "Test Provider",
                    "page_url": "https://example.org/source",
                    "licence": "Test licence",
                    "licence_url": "https://example.org/licence",
                    "asset_type": "source_image",
                }), encoding="utf-8")
                refs = [f"source-{index}"]
                scenes.append({
                    "say": f"Scene narration {index}",
                    "caption": f"Caption {index}",
                    "image": f"Visual {index}",
                    "source_ids": refs,
                    "asset_path": f"output/daily_news/asset_review/assets/scene_{index:02d}.jpg",
                    "asset_type": "image",
                    "ai_disclosure": False,
                })
                inventory.append({"scene": index, "type": "image", "path": scenes[-1]["asset_path"]})
                approved_scenes.append({
                    "say": f"Scene narration {index}",
                    "caption": f"Caption {index}",
                    "image": f"Visual {index}",
                    "source_ids": refs,
                })
            (artifact / "production_candidate.json").write_text(json.dumps({"scenes": scenes}), encoding="utf-8")
            (artifact / "asset_review_manifest.json").write_text(json.dumps({"assets": inventory}), encoding="utf-8")
            approved = root / "approved.json"
            approved.write_text(json.dumps({
                "editorial_status": "approved_for_production",
                "sources": [{"source_id": f"source-{i}"} for i in range(1, 5)],
                "scenes": approved_scenes,
            }), encoding="utf-8")
            output = root / "render-input" / "approved.json"
            asset_dir = root / "render-input" / "assets"
            attach_assets(artifact, approved, output, asset_dir)
            rendered = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(rendered["scenes"]), 4)
            for index, scene in enumerate(rendered["scenes"], 1):
                target = Path(scene["asset_path"])
                self.assertTrue(target.is_file())
                self.assertTrue(target.with_suffix(target.suffix + ".source.json").is_file())
                self.assertEqual(scene["asset_type"], "image")
                self.assertEqual(target.name, f"scene_{index:02d}.jpg")

    def test_rejects_changed_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact"
            (artifact / "assets").mkdir(parents=True)
            scenes, inventory, approved_scenes = [], [], []
            for index in range(1, 5):
                asset = artifact / "assets" / f"scene_{index:02d}.jpg"
                asset.write_bytes(b"x")
                asset.with_suffix(".jpg.source.json").write_text(json.dumps({"provider":"P","page_url":"https://example.org"}))
                scenes.append({"say":f"original {index}","source_ids":[f"s{index}"],"asset_path":f"output/daily_news/asset_review/assets/scene_{index:02d}.jpg","asset_type":"image"})
                inventory.append({"path":scenes[-1]["asset_path"]})
                approved_scenes.append({"say":f"changed {index}","source_ids":[f"s{index}"]})
            (artifact / "production_candidate.json").write_text(json.dumps({"scenes":scenes}))
            (artifact / "asset_review_manifest.json").write_text(json.dumps({"assets":inventory}))
            approved=root/"approved.json"
            approved.write_text(json.dumps({"editorial_status":"approved_for_production","scenes":approved_scenes}))
            with self.assertRaisesRegex(ValueError, "narration differs"):
                attach_assets(artifact, approved, root/"out.json", root/"assets-out")


if __name__ == "__main__":
    unittest.main()
