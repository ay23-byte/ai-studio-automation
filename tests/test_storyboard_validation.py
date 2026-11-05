import unittest
from core.script_engine import validate_longform_storyboard, _repair_longform_visual_anchors

class StoryboardValidationTests(unittest.TestCase):
    def setUp(self):
        self.script = {"sections": [{
            "narration": "The star collapses. Then the event horizon forms. Finally light bends around it.",
            "visuals": [
                {"narration_anchor": "The star collapses", "visual_prompt": "A massive star collapsing", "keywords": ["stellar collapse"]},
                {"narration_anchor": "the event horizon forms", "visual_prompt": "A black hole event horizon visualization", "keywords": ["event horizon"]},
                {"narration_anchor": "light bends around it", "visual_prompt": "Gravitational lensing around a black hole", "keywords": ["gravitational lensing"]}
            ]
        }]}

    def test_accepts_exact_ordered_anchors(self):
        self.assertIs(validate_longform_storyboard(self.script), self.script)

    def test_rejects_anchor_missing_from_narration(self):
        self.script["sections"][0]["visuals"][1]["narration_anchor"] = "black hole evaporates"
        with self.assertRaisesRegex(ValueError, "anchor is absent or out of order"):
            validate_longform_storyboard(self.script)

    def test_rejects_out_of_order_anchors(self):
        visuals = self.script["sections"][0]["visuals"]
        visuals[0]["narration_anchor"], visuals[2]["narration_anchor"] = visuals[2]["narration_anchor"], visuals[0]["narration_anchor"]
        with self.assertRaisesRegex(ValueError, "anchor is absent or out of order"):
            validate_longform_storyboard(self.script)

    def test_repairs_missing_and_out_of_order_model_anchors(self):
        import copy
        script = copy.deepcopy(self.script)
        visuals = script["sections"][0]["visuals"]
        visuals[1]["narration_anchor"] = "not copied exactly"
        visuals[2]["narration_anchor"] = "The star collapses"
        repaired = _repair_longform_visual_anchors(script)
        self.assertIs(validate_longform_storyboard(repaired), repaired)
        narration = repaired["sections"][0]["narration"]
        cursor = 0
        for beat in repaired["sections"][0]["visuals"]:
            position = narration.casefold().find(beat["narration_anchor"].casefold(), cursor)
            self.assertGreaterEqual(position, 0)
            cursor = position + len(beat["narration_anchor"])

    def test_rejects_missing_visual_prompt(self):
        self.script["sections"][0]["visuals"][0]["visual_prompt"] = ""
        with self.assertRaisesRegex(ValueError, "missing anchor, visual prompt or stock keywords"):
            validate_longform_storyboard(self.script)

if __name__ == "__main__":
    unittest.main()
