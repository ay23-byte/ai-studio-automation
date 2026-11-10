"""Tests for the provider-independent cinematic shot planner."""
import json
import tempfile
import unittest
from pathlib import Path

from core.cinematic_shot_planner import (
    build_cinematic_prompt, plan_cinematic_shots, write_cinematic_shot_plan, _movement_hint,
)


class CinematicShotPlannerTests(unittest.TestCase):
    def test_prompt_uses_camera_controls(self):
        prompt = build_cinematic_prompt(
            "Aerial view of a coastal town", camera="70mm_film", lens="anamorphic",
            focal_length=50, aperture="f/2.8", movement="aerial",
            shot_size="extreme_wide", lighting="golden_hour",
        )
        for term in ("70mm film camera", "anamorphic", "50mm",
                     "smooth aerial", "extreme wide", "golden-hour"):
            self.assertIn(term, prompt)

    def test_invalid_settings_fall_back_safely(self):
        prompt = build_cinematic_prompt(
            "A research laboratory", camera="unknown", focal_length=999,
            aperture="f/99", aspect_ratio="3:7", movement="teleport",
        )
        self.assertIn("full-frame digital cinema camera", prompt)
        self.assertIn("35mm", prompt)
        self.assertIn("16:9 composition", prompt)
        self.assertIn("locked-off camera", prompt)

    def test_natural_language_camera_movement_is_normalized(self):
        self.assertEqual(_movement_hint("slow lateral tracking movement"), "tracking")
        self.assertEqual(_movement_hint("slow controlled dolly push-in"), "slow_push_in")
        self.assertEqual(_movement_hint("locked-off camera"), "locked")

    def test_empty_subject_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cinematic_prompt("  ")

    def test_plan_preserves_source_and_does_not_mutate_input(self):
        source = [{"narration": "A scientist studies the sample.",
                   "camera_movement": "tracking", "cinema_settings": {"lens": "macro"}}]
        result = plan_cinematic_shots(source)
        self.assertNotIn("cinematic_prompt", source[0])
        self.assertEqual(result[0]["shot_id"], "shot_0001")
        self.assertIn("macro cinema lens", result[0]["cinematic_prompt"])
        self.assertEqual(result[0]["generation_status"], "prompt_only")
        self.assertEqual(result[0]["generation_backend"], "not_configured")
        # Renderer consumes this field directly; it must be a normalized control key.
        self.assertEqual(result[0]["cinema_settings"]["movement"], "tracking")

    def test_export_is_explicitly_prompt_only_and_no_paid_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "review" / "cinema_plan.json"
            report = write_cinematic_shot_plan(
                [{"subject": "An archival control room"}], target
            )
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(report, saved)
            self.assertFalse(saved["generation_performed"])
            self.assertFalse(saved["paid_provider_calls_performed"])
            self.assertTrue(saved["human_review_required"])


if __name__ == "__main__":
    unittest.main()
