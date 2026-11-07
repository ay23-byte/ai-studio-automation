import json
import tempfile
import unittest
from pathlib import Path

from core.editorial_review import (
    ReviewStoreError, audit_visual_reviews, load_reviews, review_store_path, save_review,
)


class EditorialReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report_path = self.root / "visual_review_report.json"
        self.media = self.root / "shot.jpg"
        self.media.write_bytes(b"fake-image")
        self.report = {"shots": [{
            "shot_id": "shot_0001", "chapter_id": "chapter_1",
            "media_path": str(self.media), "sample_frames": [],
        }]}
        self.report_path.write_text(json.dumps(self.report), encoding="utf-8")

    def test_missing_store_initializes_pending_without_approval(self):
        store = load_reviews(self.report_path, self.report["shots"])
        record = store["shots"]["shot_0001"]
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["source_review_status"], "pending")
        self.assertFalse(audit_visual_reviews(self.report, store)["publication_ready"])

    def test_approve_persists_across_reload_and_gate_requires_source_review(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved")
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertEqual(store["shots"]["shot_0001"]["status"], "approved")
        self.assertFalse(audit_visual_reviews(self.report, store)["publication_ready"])
        save_review(self.report_path, "shot_0001", source_review_status="reviewed")
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertTrue(audit_visual_reviews(self.report, store)["publication_ready"])

    def test_rejection_requires_reason_and_is_blocking(self):
        load_reviews(self.report_path, self.report["shots"])
        with self.assertRaises(ReviewStoreError):
            save_review(self.report_path, "shot_0001", status="rejected")
        save_review(self.report_path, "shot_0001", status="rejected", rejection_reason="Wrong location")
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertFalse(audit_visual_reviews(self.report, store)["publication_ready"])

    def test_replacement_request_is_persistent_and_blocking(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="needs_replacement")
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertTrue(store["shots"]["shot_0001"]["replacement_requested"])
        self.assertFalse(audit_visual_reviews(self.report, store)["publication_ready"])

    def test_chapter_filter_metadata_is_preserved(self):
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertEqual(store["shots"]["shot_0001"]["chapter_id"], "chapter_1")

    def test_corrupt_json_is_reported_without_overwrite(self):
        path = review_store_path(self.report_path)
        original = "{broken"
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(ReviewStoreError):
            load_reviews(self.report_path, self.report["shots"])
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_missing_media_blocks_publication(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        self.media.unlink()
        store = load_reviews(self.report_path, self.report["shots"])
        result = audit_visual_reviews(self.report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("media is missing" in issue["message"] for issue in result["issues"]))

    def test_new_shot_is_added_without_resetting_existing_decisions(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        shots = self.report["shots"] + [{"shot_id": "shot_0002", "chapter_id": "chapter_2"}]
        store = load_reviews(self.report_path, shots)
        self.assertEqual(store["shots"]["shot_0001"]["status"], "approved")
        self.assertEqual(store["shots"]["shot_0002"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
