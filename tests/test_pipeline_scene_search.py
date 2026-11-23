import ast
import inspect
import unittest
from unittest.mock import patch

from core import pipeline
from core.pipeline import _scene_search_brief, _short_scene_search_brief, _generate_scene_image


class SceneSearchBriefTests(unittest.TestCase):
    def test_uses_subject_and_specific_scene_anchors(self):
        beat = {
            "subject": "EUV lithography machine",
            "location": "Veldhoven, Netherlands",
            "time_period": "modern semiconductor fab",
            "anchor": "A machine projects extreme ultraviolet light onto a silicon wafer.",
        }
        brief = _scene_search_brief(beat, ["ASML EUV", "semiconductor wafer"])
        self.assertIn("EUV lithography machine", brief)
        self.assertIn("Veldhoven", brief)
        self.assertIn("ASML EUV", brief)
        self.assertIn("semiconductor wafer", brief)
        self.assertNotIn("silicon wafer", brief)
        self.assertNotIn("projects extreme ultraviolet light", brief)

    def test_does_not_use_generic_cinematic_generation_instructions(self):
        beat = {
            "subject": "semiconductor wafer",
            "anchor": "Close-up of a silicon wafer inside a cleanroom.",
            "visual_prompt": "Photorealistic premium 16:9 B-roll, 35mm lens, slow dolly, natural lighting",
        }
        brief = _scene_search_brief(beat, ["silicon wafer", "cleanroom"])
        self.assertNotIn("Photorealistic", brief)
        self.assertNotIn("35mm", brief)
        self.assertNotIn("dolly", brief)

    def test_shorts_search_uses_visual_direction_and_keywords_not_narration(self):
        scene = {
            "say": "The Hindu noted that the rupee rose six paise against the US dollar.",
            "image": "Indian rupee banknotes beside a foreign exchange trading screen",
            "keywords": ["Indian rupee", "forex market", "currency exchange"],
        }
        brief = _short_scene_search_brief(scene)
        self.assertIn("Indian rupee banknotes", brief)
        self.assertIn("forex market", brief)
        self.assertNotIn("The Hindu noted", brief)
        self.assertNotIn("six paise", brief)

    def test_shorts_search_falls_back_to_narration_only_when_visual_data_is_absent(self):
        brief = _short_scene_search_brief({"say": "A solar flare disrupts radio communication."})
        self.assertIn("solar flare", brief)

    def test_empty_inputs_are_safe_and_output_is_bounded(self):
        self.assertEqual(_scene_search_brief({}, []), "")
        brief = _scene_search_brief({"subject": "x" * 1000}, [])
        self.assertLessEqual(len(brief), 600)

    def test_image_provider_uses_concise_scene_query_not_generation_prompt(self):
        prompt = "Photorealistic cinematic shot; narration wrapper and camera details"
        brief = "desalination plant reverse osmosis membranes industrial water treatment"
        with patch("core.pipeline.generate_image", return_value=True) as provider:
            self.assertTrue(_generate_scene_image(
                prompt, "scene.jpg", category="science", search_query=brief
            ))
        provider.assert_called_once_with(
            prompt, "scene.jpg", category="science", search_query=brief
        )

    def test_longform_image_provider_calls_pass_scene_search_brief(self):
        tree = ast.parse(inspect.getsource(pipeline))
        image_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_generate_scene_image"
        ]
        self.assertEqual(len(image_calls), 3, "all long-form image acquisitions must use the helper")
        for call in image_calls:
            self.assertIn("search_query", {keyword.arg for keyword in call.keywords})
        direct_provider_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "generate_image"
        ]
        self.assertEqual(len(direct_provider_calls), 1, "only the helper may call the image provider directly")


if __name__ == "__main__":
    unittest.main()
