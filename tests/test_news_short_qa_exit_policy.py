import unittest

from scripts.qa_news_short import exit_code_for_status


class NewsShortQaExitPolicyTests(unittest.TestCase):
    def test_blocking_failure_fails_workflow(self):
        self.assertEqual(exit_code_for_status("FAIL"), 1)

    def test_review_is_a_human_review_handoff_not_a_ci_failure(self):
        self.assertEqual(exit_code_for_status("REVIEW"), 0)

    def test_pass_succeeds(self):
        self.assertEqual(exit_code_for_status("PASS"), 0)

    def test_unknown_status_does_not_claim_a_blocking_failure(self):
        # The report itself retains the explicit human-review requirement.
        self.assertEqual(exit_code_for_status("NOT_RUN"), 0)


if __name__ == "__main__":
    unittest.main()
