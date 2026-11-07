import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.editorial_review import (
    ReviewStoreError, audit_visual_reviews, load_reviews, review_store_path, save_review,
)
from core.workflow_audit import build_workflow_report


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
        save_review(
            self.report_path, "shot_0001", source_review_status="reviewed",
            rights_review_status="reviewed", rights_notes="Checked original asset terms.",
            licence_name="Original asset / permission confirmed",
        )
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

    def test_rights_review_is_separate_and_blocks_until_documented(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        store = load_reviews(self.report_path, self.report["shots"])
        result = audit_visual_reviews(self.report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("Rights review status" in issue["message"] for issue in result["issues"]))
        save_review(
            self.report_path, "shot_0001", rights_review_status="reviewed",
            rights_notes="Reviewed provider terms and reuse restrictions.",
            licence_name="Provider licence reviewed",
        )
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertTrue(audit_visual_reviews(self.report, store)["publication_ready"])

    def test_schema_v1_migrates_preserving_approval_but_rights_pending(self):
        path = review_store_path(self.report_path)
        shot = self.report["shots"][0]
        legacy_identity = {
            "chapter_id": "chapter_1", "media_path": shot.get("media_path"),
            "visual_prompt": shot.get("visual_prompt"), "narration": shot.get("narration"),
            "media_type": shot.get("media_type"),
        }
        legacy_fingerprint = hashlib.sha256(
            json.dumps(legacy_identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps({
            "schema_version": 1, "updated_at": None,
            "shots": {"shot_0001": {
                "shot_id": "shot_0001", "chapter_id": "chapter_1",
                "status": "approved", "reviewer_notes": "old review",
                "rejection_reason": "", "replacement_requested": False,
                "reviewed_at": None, "source_review_status": "reviewed",
                "shot_fingerprint": legacy_fingerprint,
            }},
        }), encoding="utf-8")
        store = load_reviews(self.report_path, self.report["shots"])
        self.assertEqual(store["schema_version"], 2)
        self.assertEqual(store["shots"]["shot_0001"]["status"], "approved")
        self.assertEqual(store["shots"]["shot_0001"]["source_review_status"], "reviewed")
        self.assertEqual(store["shots"]["shot_0001"]["rights_review_status"], "pending")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 2)

    def test_source_metadata_change_invalidates_rights_and_editorial_decision(self):
        shot = dict(self.report["shots"][0], source={
            "provider": "Pixabay", "asset_id": "asset-1",
            "licence_status": "Pixabay terms; attribution review required",
        })
        load_reviews(self.report_path, [shot])
        save_review(
            self.report_path, "shot_0001", status="approved",
            source_review_status="reviewed", rights_review_status="reviewed",
            rights_notes="Checked asset licence and attribution.", licence_name="Pixabay licence",
        )
        changed = dict(shot, source=dict(shot["source"], licence_status="Updated terms; re-check"))
        store = load_reviews(self.report_path, [changed])
        self.assertEqual(store["shots"]["shot_0001"]["status"], "pending")
        self.assertEqual(store["shots"]["shot_0001"]["rights_review_status"], "pending")


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

    def test_workflow_report_exposes_mandatory_publication_block(self):
        report = build_workflow_report({}, [], [], [], visual_review_path=self.report_path)
        self.assertTrue(report["summary"]["publication_blocked"])
        self.assertEqual(report["summary"]["publish_recommendation"], "blocked_by_editorial_review")
        self.assertTrue(any(issue["stage"] == "visual_review" for issue in report["issues"]))

    def test_changed_shot_invalidates_previous_approval(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        changed = [dict(self.report["shots"][0], visual_prompt="A different scene")]
        store = load_reviews(self.report_path, changed)
        self.assertEqual(store["shots"]["shot_0001"]["status"], "pending")
        self.assertEqual(store["shots"]["shot_0001"]["source_review_status"], "pending")

    def test_new_shot_is_added_without_resetting_existing_decisions(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        shots = self.report["shots"] + [{"shot_id": "shot_0002", "chapter_id": "chapter_2"}]
        store = load_reviews(self.report_path, shots)
        self.assertEqual(store["shots"]["shot_0001"]["status"], "approved")
        self.assertEqual(store["shots"]["shot_0002"]["status"], "pending")


    def test_semantic_mismatch_blocks_even_human_approved_shot(self):
        load_reviews(self.report_path, self.report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        report = dict(self.report, semantic_screening_enabled=True, shots=[
            dict(self.report["shots"][0], semantic_screening={"status": "mismatch"})
        ])
        store = load_reviews(self.report_path, report["shots"])
        result = audit_visual_reviews(report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("screening is 'mismatch'" in issue["message"]
                            for issue in result["issues"]))

    def test_semantic_match_does_not_replace_human_editorial_approval(self):
        report = dict(self.report, semantic_screening_enabled=True, shots=[
            dict(self.report["shots"][0], semantic_screening={"status": "match", "match_score": 0.9})
        ])
        store = load_reviews(self.report_path, report["shots"])
        result = audit_visual_reviews(report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("Editorial status is 'pending'" in issue["message"]
                            for issue in result["issues"]))


    def test_severe_visual_quality_failure_blocks_publication(self):
        report = dict(self.report, shots=[
            dict(self.report["shots"][0], visual_quality={
                "status": "fail", "issues": ["Visual is severely underexposed."]
            })
        ])
        load_reviews(self.report_path, report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        store = load_reviews(self.report_path, report["shots"])
        result = audit_visual_reviews(report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("Visual quality check failed" in issue["message"]
                            for issue in result["issues"]))


    def test_presenter_av_sync_failure_blocks_publication(self):
        report = dict(self.report, shots=[
            dict(self.report["shots"][0], presenter_mode="talking_avatar",
                 presenter_sync_status="FAIL")
        ])
        load_reviews(self.report_path, report["shots"])
        save_review(self.report_path, "shot_0001", status="approved", source_review_status="reviewed")
        store = load_reviews(self.report_path, report["shots"])
        result = audit_visual_reviews(report, store)
        self.assertFalse(result["publication_ready"])
        self.assertTrue(any("Presenter A/V synchronization validation failed" in issue["message"]
                            for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()
