import os
import unittest
from unittest.mock import patch

from core.pipeline import measured_runtime_report


class MeasuredDocumentaryRuntimeBoundsTests(unittest.TestCase):
    def test_narration_within_bounds_passes(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LONGFORM_MIN_MEASURED_RUNTIME_FRACTION", None)
            os.environ.pop("LONGFORM_MAX_MEASURED_RUNTIME_FRACTION", None)
            report = measured_runtime_report(
                {"runtime_validation": {"target_minutes": 18}},
                "word " * 2400,
                18 * 60,
            )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["maximum_fraction"], 1.10)

    def test_overlong_narration_fails_before_visual_production(self):
        report = measured_runtime_report(
            {"runtime_validation": {"target_minutes": 18}},
            "word " * 2916,
            20.24 * 60,
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["fraction_of_target"], 1.10)
        self.assertIn("over-length documentary", report["issue"])

    def test_underlength_narration_still_fails(self):
        report = measured_runtime_report(
            {"runtime_validation": {"target_minutes": 18}},
            "word " * 1800,
            12 * 60,
        )
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("under-length documentary", report["issue"])

    def test_configured_ceiling_cannot_be_below_floor(self):
        with patch.dict(os.environ, {
            "LONGFORM_MIN_MEASURED_RUNTIME_FRACTION": "0.9",
            "LONGFORM_MAX_MEASURED_RUNTIME_FRACTION": "0.85",
        }):
            with self.assertRaisesRegex(ValueError, "must be >="):
                measured_runtime_report(
                    {"runtime_validation": {"target_minutes": 18}},
                    "word " * 2500,
                    18 * 60,
                )


if __name__ == "__main__":
    unittest.main()
