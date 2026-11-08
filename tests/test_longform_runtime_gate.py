import unittest

from core.script_engine import count_longform_narration_words, validate_longform_runtime


class LongformRuntimeGateTests(unittest.TestCase):
    def test_counts_only_section_narration_including_devanagari(self):
        script = {"title": "Ignored title words", "sections": [
            {"title": "Ignored chapter title", "narration": "This is narration."},
            {"narration": "यह एक परीक्षण है।"},
            {"narration": "Research-based evidence matters."},
        ]}
        self.assertEqual(count_longform_narration_words(script), 11)

    def test_accepts_script_at_minimum_runtime_floor_and_records_metrics(self):
        script = {"sections": [{"narration": " ".join(["evidence"] * 1160)}]}
        result = validate_longform_runtime(script, target_minutes=10)
        self.assertEqual(result["narration_word_count"], 1160)
        self.assertEqual(result["runtime_validation"]["minimum_words"], 1160)
        self.assertAlmostEqual(result["estimated_narration_minutes"], 8.0)

    def test_rejects_underlength_script_with_actionable_diagnostic(self):
        script = {"sections": [{"narration": " ".join(["short"] * 1159)}]}
        with self.assertRaisesRegex(ValueError, "1159 spoken words"):
            validate_longform_runtime(script, target_minutes=10)

    def test_rejects_invalid_runtime_configuration(self):
        with self.assertRaises(ValueError):
            validate_longform_runtime({"sections": []}, target_minutes=0)


if __name__ == "__main__":
    unittest.main()
