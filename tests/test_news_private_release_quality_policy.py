import unittest

from core.news_private_release import _quality_report_allows_private_release


class NewsPrivateReleaseQualityPolicyTests(unittest.TestCase):
    def test_plain_pass_is_allowed(self):
        allowed, reason = _quality_report_allows_private_release({"status": "PASS"})
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_review_with_fallback_within_configured_limit_is_allowed_for_private_review(self):
        allowed, reason = _quality_report_allows_private_release({
            "status": "REVIEW",
            "visual_fallback": {
                "status": "PASS",
                "fallback_card_count": 1,
                "visual_count": 5,
                "max_share_allowed": 0.5,
            },
        })
        self.assertTrue(allowed)
        self.assertIn("human review", reason.lower())

    def test_review_without_explicit_fallback_pass_is_blocked(self):
        allowed, _ = _quality_report_allows_private_release({"status": "REVIEW"})
        self.assertFalse(allowed)

    def test_exceeded_or_explicitly_blocked_fallback_is_blocked(self):
        report = {
            "status": "REVIEW",
            "visual_fallback": {
                "status": "PASS",
                "fallback_card_count": 2,
                "visual_count": 5,
                "max_share_allowed": 0.2,
            },
        }
        allowed, _ = _quality_report_allows_private_release(report)
        self.assertFalse(allowed)
        report["upload_blocked"] = True
        allowed, _ = _quality_report_allows_private_release(report)
        self.assertFalse(allowed)

    def test_failed_or_unknown_quality_status_is_blocked(self):
        for status in ("FAIL", "ERROR", "NOT_RUN", ""):
            with self.subTest(status=status):
                allowed, _ = _quality_report_allows_private_release({"status": status})
                self.assertFalse(allowed)

    def test_malformed_fallback_evidence_is_blocked(self):
        allowed, _ = _quality_report_allows_private_release({
            "status": "REVIEW",
            "visual_fallback": {
                "status": "PASS",
                "fallback_card_count": 1,
                "visual_count": 0,
                "max_share_allowed": 0.5,
            },
        })
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
