import unittest
from pathlib import Path

from scripts.package_build_for_review import summary_markdown


class ReviewSummaryUploadStatusTests(unittest.TestCase):
    def test_daily_news_review_does_not_claim_private_upload_did_not_happen(self):
        summary = {
            "status": "REVIEW_REQUIRED", "job_id": 7, "topic": "Example news",
            "video_sha256": "abc123", "qa_statuses": {"technical": "PASS", "semantic": "REVIEW"},
        }
        text = summary_markdown("daily_news", Path("shared_review_package"), summary)
        self.assertIn("private-upload status", text)
        self.assertNotIn("Nothing was uploaded", text)

    def test_daily_news_failure_does_not_claim_nothing_was_uploaded(self):
        summary = {
            "status": "FAIL", "job_id": 7, "topic": "Example news",
            "video_sha256": "abc123", "qa_statuses": {"technical": "FAIL"},
        }
        text = summary_markdown("daily_news", Path("shared_review_package"), summary)
        self.assertIn("private-upload status", text)
        self.assertIn("nothing was made public", text)
        self.assertNotIn("Nothing was uploaded", text)

    def test_standard_short_review_still_says_no_upload(self):
        summary = {
            "status": "REVIEW_REQUIRED", "job_id": 7, "topic": "Example short",
            "video_sha256": "abc123", "qa_statuses": {"technical": "PASS"},
        }
        text = summary_markdown("shortform", Path("shared_review_package"), summary)
        self.assertIn("Nothing was uploaded", text)


if __name__ == "__main__":
    unittest.main()
