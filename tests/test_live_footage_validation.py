import unittest
from unittest.mock import patch

from scripts.check_footage_sources_live import run_checks


class LiveFootageValidationTests(unittest.TestCase):
    def test_zero_candidates_fails_overall_even_when_requests_succeed(self):
        result = run_checks("topic", providers=[
            ("provider-a", lambda query: []),
            ("provider-b", lambda query: []),
        ])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["total_eligible_candidates"], 0)
        self.assertEqual(result["providers"][0]["status"], "no_eligible_results")

    def test_any_provider_candidate_meets_combined_minimum(self):
        result = run_checks("topic", min_total=2, providers=[
            ("provider-a", lambda query: [1]),
            ("provider-b", lambda query: [1, 2]),
        ])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["total_eligible_candidates"], 9)

    def test_topic_variants_are_checked(self):
        seen = []
        run_checks("India disappearing villages", providers=[
            ("provider", lambda query: seen.append(query) or []),
        ])
        self.assertIn("India village rural life", seen)
        self.assertGreater(len(seen), 1)

    def test_north_korea_title_expands_to_grounded_stock_search_terms(self):
        from scripts.check_footage_sources_live import _query_variants
        queries = _query_variants("North Korea: The Country That Hides Itself")
        self.assertIn("North Korea", queries)
        self.assertIn("Pyongyang", queries)
        self.assertIn("Korean peninsula", queries)
        self.assertLessEqual(len(queries), 8)

    def test_provider_exception_is_reported_and_other_results_count(self):
        def broken(query):
            raise RuntimeError("provider offline")
        result = run_checks("topic", providers=[
            ("broken", broken),
            ("working", lambda query: [1]),
        ])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["providers"][0]["status"], "error")
        self.assertIn("provider offline", result["providers"][0]["errors"][0])

    def test_diagnostic_only_reports_warning_without_weakening_strict_default(self):
        from scripts.check_footage_sources_live import main
        import sys
        failed = {"status": "failed", "total_eligible_candidates": 0, "providers": [], "minimum_total_candidates": 1}
        with patch.object(sys, "argv", ["check_footage_sources_live", "North Korea", "--diagnostic-only"]):
            with patch("scripts.check_footage_sources_live.run_checks", return_value=dict(failed)):
                with patch("builtins.print") as output:
                    main()
        self.assertTrue(output.called)

    def test_strict_default_still_fails_when_no_candidates_are_found(self):
        from scripts.check_footage_sources_live import main
        import sys
        failed = {"status": "failed", "total_eligible_candidates": 0, "providers": [], "minimum_total_candidates": 1}
        with patch.object(sys, "argv", ["check_footage_sources_live", "topic"]):
            with patch("scripts.check_footage_sources_live.run_checks", return_value=dict(failed)):
                with self.assertRaises(SystemExit):
                    main()


if __name__ == "__main__":
    unittest.main()
