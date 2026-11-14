import unittest

from core.pipeline import _scene_search_brief


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

    def test_empty_inputs_are_safe_and_output_is_bounded(self):
        self.assertEqual(_scene_search_brief({}, []), "")
        brief = _scene_search_brief({"subject": "x" * 1000}, [])
        self.assertLessEqual(len(brief), 600)


if __name__ == "__main__":
    unittest.main()
