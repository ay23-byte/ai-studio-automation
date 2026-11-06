import unittest

from core.script_engine import (
    _repair_longform_visual_anchors,
    validate_longform_storyboard,
)


class LongformVisualAnchorRepairTests(unittest.TestCase):
    def test_repairs_missing_and_out_of_order_anchors(self):
        narration = (
            "The company arrived as traders. It built warehouses near the port. "
            "Later its army fought at Plassey. Revenue collection expanded inland. "
            "The Crown took control after the uprising."
        )
        script = {
            "sections": [{
                "narration": narration,
                "visuals": [
                    {"narration_anchor": "wrong phrase", "visual_prompt": "Trading ships", "keywords": ["East India Company"]},
                    {"narration_anchor": "The Crown took control", "visual_prompt": "Company army", "keywords": ["Plassey battle"]},
                    {"narration_anchor": "It built warehouses", "visual_prompt": "Revenue records", "keywords": ["colonial revenue"]},
                ],
            }]
        }

        repaired = _repair_longform_visual_anchors(script)
        self.assertIs(repaired, script)
        validate_longform_storyboard(repaired)
        anchors = [beat["narration_anchor"] for beat in repaired["sections"][0]["visuals"]]
        positions = [narration.find(anchor) for anchor in anchors]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertEqual(positions, sorted(positions))

    def test_preserves_already_valid_exact_anchors(self):
        script = {
            "sections": [{
                "narration": "The company arrived. It built a fort. The army advanced.",
                "visuals": [
                    {"narration_anchor": "The company arrived", "visual_prompt": "Ships", "keywords": ["ships"]},
                    {"narration_anchor": "It built a fort", "visual_prompt": "Fort", "keywords": ["fort"]},
                    {"narration_anchor": "The army advanced", "visual_prompt": "Army", "keywords": ["army"]},
                ],
            }]
        }
        original = [beat["narration_anchor"] for beat in script["sections"][0]["visuals"]]
        _repair_longform_visual_anchors(script)
        self.assertEqual(
            [beat["narration_anchor"] for beat in script["sections"][0]["visuals"]],
            original,
        )
        validate_longform_storyboard(script)


if __name__ == "__main__":
    unittest.main()
