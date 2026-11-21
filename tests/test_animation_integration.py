"""Fast, dependency-free checks for conservative animation selection."""
import unittest

from core.animation_integration import select_animation_kind


class AnimationSelectionTests(unittest.TestCase):
    def test_supported_manim_concepts(self):
        cases = [
            ("A sine wave represents periodic motion.", "manim_wave"),
            ("Projectile motion follows a parabolic path.", "manim_projectile"),
            ("Vector addition combines two forces.", "manim_vectors"),
            ("Calculus uses the derivative of a function.", "manim_calculus"),
            ("Electric field lines show the field direction.", "manim_field_lines"),
            ("A Fourier series combines harmonic components.", "manim_fourier"),
            ("A neural network has input and output layers.", "manim_neural_network"),
        ]
        for narration, expected in cases:
            with self.subTest(narration=narration):
                self.assertEqual(select_animation_kind(narration, "diagram"), expected)

    def test_supported_blender_concepts(self):
        cases = [
            ("A circular orbit around a central body.", "blender_orbit"),
            ("The solar system has planets orbiting the Sun.", "blender_solar_system"),
            ("An atom contains electrons orbiting the nucleus.", "blender_atom"),
            ("The Earth-Moon system has a stable orbital relationship.", "blender_earth_moon"),
            ("A rotating Earth globe illustrates global geography.", "blender_earth_globe"),
            ("A volcanic eruption exposes the magma chamber.", "blender_volcano"),
            ("A satellite orbit carries a spacecraft around Earth.", "blender_satellite"),
            ("Wind turbine blades turn as the rotor spins.", "blender_turbine"),
        ]
        for narration, expected in cases:
            with self.subTest(narration=narration):
                self.assertEqual(select_animation_kind(narration, "animation"), expected)

    def test_map_visual_can_select_a_geography_explainer(self):
        self.assertEqual(select_animation_kind("A rotating Earth globe illustrates global geography.", "map"),
                         "blender_earth_globe")

    def test_negated_concept_is_never_animated(self):
        self.assertIsNone(select_animation_kind("This is not a sine wave.", "diagram"))

    def test_multiple_concepts_are_ambiguous(self):
        self.assertIsNone(select_animation_kind("Compare a sine wave and a circular orbit.", "diagram"))

    def test_unrelated_visual_type_is_not_replaced(self):
        self.assertIsNone(select_animation_kind(
            "A circular orbit around a central body.", "archival", "historical footage"))

    def test_missing_concept_is_not_guessed(self):
        self.assertIsNone(select_animation_kind("The satellite moves around Earth.", "diagram"))

    def test_negation_in_visual_prompt_does_not_override_narration(self):
        self.assertEqual(select_animation_kind(
            "A circular orbit around a central body.", "diagram", "scientific illustration"),
            "blender_orbit")


if __name__ == "__main__":
    unittest.main()
