"""Fast, dependency-free checks for conservative automatic animation selection."""
import unittest

from core.animation_integration import select_animation_kind


class AnimationSelectionTests(unittest.TestCase):
    def test_positive_sine_wave_scene_selects_manim(self):
        self.assertEqual(
            select_animation_kind("A sine wave represents periodic motion.", "diagram"),
            "manim_wave",
        )

    def test_positive_circular_orbit_scene_selects_blender(self):
        self.assertEqual(
            select_animation_kind("A circular orbit around a central body.", "animation"),
            "blender_orbit",
        )

    def test_negated_concept_is_never_animated(self):
        self.assertIsNone(
            select_animation_kind("This is not a sine wave.", "diagram")
        )

    def test_ambiguous_scene_with_both_concepts_is_not_animated(self):
        self.assertIsNone(
            select_animation_kind("Compare a sine wave and a circular orbit.", "diagram")
        )

    def test_unrelated_visual_type_is_not_replaced(self):
        self.assertIsNone(
            select_animation_kind("A circular orbit around a central body.", "archival", "historical footage")
        )

    def test_missing_concept_is_not_guessed(self):
        self.assertIsNone(
            select_animation_kind("The satellite moves around Earth.", "diagram")
        )


if __name__ == "__main__":
    unittest.main()
