import json
import tempfile
import unittest
from pathlib import Path

from core.news_short_production import validate_render_inputs, _ass_time


class NewsShortProductionTests(unittest.TestCase):
    def _fixture(self, root):
        scenes = []
        for i in range(4):
            asset = root / f"scene{i}.jpg"
            asset.write_bytes(b"placeholder")
            asset.with_suffix(".jpg.source.json").write_text(json.dumps({
                "provider": "Test source", "page_url": "https://example.org/asset",
                "licence": "CC BY 4.0",
                "licence_url": "https://creativecommons.org/licenses/by/4.0/",
                "asset_type": "photo"
            }), encoding="utf-8")
            scenes.append({"say": f"Scene {i} narration.", "caption": f"Scene {i}",
                           "source_ids": ["S1"], "asset_type": "image",
                           "asset_path": str(asset)})
        script = {"editorial_status": "approved_for_production",
                  "sources": [{"source_id": "S1"}], "scenes": scenes}
        return script

    def test_requires_separate_production_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            script = self._fixture(Path(temp))
            with self.assertRaisesRegex(ValueError, "separate production approval"):
                validate_render_inputs(script, None)

    def test_requires_explicit_script_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            script = self._fixture(Path(temp))
            script["editorial_status"] = "draft_requires_human_script_and_visual_review"
            with self.assertRaisesRegex(ValueError, "approved_for_production"):
                validate_render_inputs(script, {"status": "approved"})

    def test_requires_known_source_refs_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            script = self._fixture(Path(temp))
            script["scenes"][0]["source_ids"] = ["UNKNOWN"]
            with self.assertRaisesRegex(ValueError, "known source_ids"):
                validate_render_inputs(script, {"status": "approved"})

    def test_ai_assets_need_disclosure_and_terms_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._fixture(root)
            asset = Path(script["scenes"][0]["asset_path"])
            asset.with_suffix(".jpg.source.json").write_text(json.dumps({
                "provider": "Image model", "page_url": "https://example.org/terms",
                "asset_type": "ai_generated_illustration", "terms_reviewed": False
            }), encoding="utf-8")
            script["scenes"][0]["ai_disclosure"] = False
            with self.assertRaisesRegex(ValueError, "AI asset"):
                validate_render_inputs(script, {"status": "approved"})

    def test_ass_time_format(self):
        self.assertEqual(_ass_time(65.239), "0:01:05.24")


if __name__ == "__main__":
    unittest.main()
