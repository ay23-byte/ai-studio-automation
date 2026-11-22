import unittest

from core.documentary_editorial_qa import audit_editorial_shots


class DocumentaryEditorialQATests(unittest.TestCase):
    def test_search_terms_are_not_allowed_as_viewer_facing_copy(self):
        report = audit_editorial_shots([{
            "visual_type": "chapter_title",
            "on_screen_text": "SOLAR FLARE FOOTAGE",
            "visual_prompt": "Solar flare erupting from the Sun",
            "_dur": 2.5,
        }])
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["issues"][0]["code"], "search_placeholder_on_screen")

    def test_long_titles_are_flagged(self):
        report = audit_editorial_shots([{
            "visual_type": "title",
            "on_screen_text": "MAGNETOSPHERE SOLAR WIND INTERACTION EXPLAINED",
            "_dur": 3,
        }])
        self.assertIn("display_text_overflow", [item["code"] for item in report["issues"]])

    def test_chart_without_real_data_is_rejected(self):
        report = audit_editorial_shots([{
            "visual_type": "chart", "on_screen_text": "Earth's core",
            "visual_prompt": "Rising bars",
        }])
        self.assertIn("chart_without_data", [item["code"] for item in report["issues"]])

    def test_repeated_identical_chart_is_flagged(self):
        spec = {"labels": ["A", "B"], "values": [1, 2], "source_refs": ["S1"]}
        report = audit_editorial_shots([
            {"visual_type": "chart", "chart_spec": spec},
            {"visual_type": "chart", "chart_spec": spec},
        ])
        self.assertIn("repeated_identical_charts", [item["code"] for item in report["issues"]])

    def test_valid_unique_chart_and_short_title_pass(self):
        report = audit_editorial_shots([{
            "visual_type": "chart",
            "chart_spec": {"labels": ["1950", "2000"], "values": [10, 12]},
            "on_screen_text": "Magnetic field strength",
            "_dur": 8,
        }])
        self.assertEqual(report["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
