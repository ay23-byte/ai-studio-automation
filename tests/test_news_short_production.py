import json
import tempfile
import unittest
from pathlib import Path

from core.news_short_production import validate_render_inputs, _ass_time, _subtitle_text, _write_ass


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

    def test_project_graphic_requires_and_accepts_font_licence_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._fixture(root)
            for scene in script["scenes"]:
                asset = Path(scene["asset_path"])
                asset.with_suffix(".jpg.source.json").write_text(json.dumps({
                    "provider": "LabLens/AI Studio local graphic renderer",
                    "asset_type": "programmatic_graphic",
                    "ai_generated": False,
                    "licence": "Project-created original graphic",
                    "font_licence": "Bitstream Vera and DejaVu font licence",
                    "font_licence_url": "https://dejavu-fonts.github.io/License.html",
                    "attribution_text": "Original graphic; DejaVu font notice."
                }), encoding="utf-8")
            self.assertEqual(len(validate_render_inputs(script, {"status": "approved"})), 4)

    def test_project_graphic_without_font_licence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = self._fixture(root)
            asset = Path(script["scenes"][0]["asset_path"])
            asset.with_suffix(".jpg.source.json").write_text(json.dumps({
                "provider": "LabLens/AI Studio local graphic renderer",
                "asset_type": "programmatic_graphic", "ai_generated": False,
                "licence": "Project-created original graphic"
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "font licence labels"):
                validate_render_inputs(script, {"status": "approved"})

    def test_subtitles_follow_hindi_narration_not_english_graphic_caption(self):
        scene = {"say": "यह हिंदी में बोली गई पंक्ति है।", "caption": "English graphic heading"}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "captions.ass"
            _write_ass([scene], [4.0], output)
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("यह हिंदी में बोली गई पंक्ति है।", rendered)
        self.assertNotIn("English graphic heading", rendered)

    def test_subtitle_style_uses_compact_lower_safe_band(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "captions.ass"
            _write_ass([{"say": "हिंदी narration"}], [4.0], output)
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("Noto Sans Devanagari,46", rendered)
        self.assertIn(",90,90,300,1", rendered)
        self.assertIn("BorderStyle", rendered)

    def test_hinglish_captions_use_latin_font_style(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "captions.ass"
            _write_ass([{"say": "Aaj ki important news samajhte hain."}], [4.0], output, language="hinglish")
            rendered = output.read_text(encoding="utf-8")
        self.assertIn("Style: News,Noto Sans,52", rendered)
        self.assertNotIn("Noto Sans Devanagari", rendered)

    def test_ass_time_format(self):
        self.assertEqual(_ass_time(65.239), "0:01:05.24")


if __name__ == "__main__":
    unittest.main()
