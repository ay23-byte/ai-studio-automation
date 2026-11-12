import json
import tempfile
import unittest
from pathlib import Path

from scripts.attach_daily_news_assets import attach_assets


class DailyNewsAssetHandoffTests(unittest.TestCase):
    def _fixture(self, root, changed_narration=False, ai=False):
        artifact = root / "artifact"
        (artifact / "assets").mkdir(parents=True)
        candidate_scenes, inventory, approved_scenes = [], [], []
        for index in range(1, 5):
            suffix = ".jpg"
            asset = artifact / "assets" / f"scene_{index:02d}{suffix}"
            asset.write_bytes(b"fake-image")
            metadata = {
                "provider": "Test Provider",
                "page_url": "https://example.org/source",
                "licence": "Test licence",
                "licence_url": "https://example.org/licence",
                "asset_type": "ai_generated_image" if ai else "source_image",
                "terms_reviewed": False if ai else None,
            }
            asset.with_suffix(suffix + ".source.json").write_text(json.dumps(metadata), encoding="utf-8")
            refs = [f"source-{index}"]
            candidate_scenes.append({
                "say": f"Scene narration {index}",
                "source_ids": refs,
                "asset_path": f"output/daily_news/asset_review/assets/scene_{index:02d}{suffix}",
                "asset_type": "image",
                "ai_disclosure": ai,
            })
            inventory.append({"scene": index, "type": "image", "path": candidate_scenes[-1]["asset_path"]})
            approved_scenes.append({
                "say": f"{'changed' if changed_narration else 'Scene narration'} {index}",
                "caption": f"Caption {index}",
                "image": f"Visual {index}",
                "source_ids": refs,
            })
        (artifact / "production_candidate.json").write_text(json.dumps({"scenes": candidate_scenes}), encoding="utf-8")
        (artifact / "asset_review_manifest.json").write_text(json.dumps({"assets": inventory}), encoding="utf-8")
        approved = root / "approved.json"
        approved.write_text(json.dumps({
            "editorial_status": "approved_for_production",
            "sources": [{"source_id": f"source-{i}"} for i in range(1, 5)],
            "scenes": approved_scenes,
        }), encoding="utf-8")
        approval = root / "approval.json"
        approval.write_text(json.dumps({"asset_reviews": [
            {"scene": i, "status": "approved", "terms_reviewed": ai} for i in range(1, 5)
        ]}), encoding="utf-8")
        return artifact, approved, approval

    def test_attaches_exact_scene_assets_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, approved, approval = self._fixture(root)
            output = root / "render-input" / "approved.json"
            asset_dir = root / "render-input" / "assets"
            attach_assets(artifact, approved, approval, output, asset_dir)
            rendered = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(rendered["scenes"]), 4)
            for index, scene in enumerate(rendered["scenes"], 1):
                target = Path(scene["asset_path"])
                self.assertTrue(target.is_file())
                self.assertTrue(target.with_suffix(target.suffix + ".source.json").is_file())
                self.assertEqual(target.name, f"scene_{index:02d}.jpg")

    def test_ai_terms_must_be_explicitly_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, approved, approval = self._fixture(root, ai=True)
            payload = json.loads(approval.read_text(encoding="utf-8"))
            for item in payload["asset_reviews"]:
                item["terms_reviewed"] = False
            approval.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "AI terms"):
                attach_assets(artifact, approved, approval, root/"out.json", root/"assets-out")

    def test_rejects_changed_narration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, approved, approval = self._fixture(root, changed_narration=True)
            with self.assertRaisesRegex(ValueError, "narration differs"):
                attach_assets(artifact, approved, approval, root/"out.json", root/"assets-out")


if __name__ == "__main__":
    unittest.main()
