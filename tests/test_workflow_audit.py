import tempfile
import unittest
from pathlib import Path

from core.workflow_audit import (
    audit_media_assets, audit_presenter_timing, audit_research,
    audit_storyboard, build_workflow_report, save_workflow_report,
)
from core.research_verification import write_research_verification_report


class WorkflowAuditTests(unittest.TestCase):
    def test_claim_without_evidence_never_becomes_verified(self):
        result = audit_research({"claims": [{"text": "A claim", "status": "verified"}]})
        self.assertEqual(result["claims"][0]["status"], "needs_verification")
        self.assertFalse(result["fact_check_complete"])

    def test_claim_with_linked_evidence_is_pending_editor_review(self):
        result = audit_research({
            "sources": [{"id": "s1", "url": "https://example.org", "title": "Example"}],
            "claims": [{"text": "Claim", "status": "verified", "source_ids": ["s1"], "evidence": "Relevant excerpt"}],
        })
        self.assertEqual(result["claims"][0]["status"], "source_supported_pending_editor_review")

    def test_storyboard_detects_missing_narration_and_overlap(self):
        result = audit_storyboard([
            {"scene_number": 1, "narration": "First", "visual_prompt": "A lab", "start_seconds": 0, "end_seconds": 5},
            {"scene_number": 2, "narration": "", "visual_prompt": "A city", "start_seconds": 4, "end_seconds": 8},
        ])
        self.assertTrue(any("overlap" in x["message"] for x in result["issues"]))
        self.assertTrue(any("narration" in x["message"] for x in result["issues"]))

    def test_media_requires_provenance_and_licence_review(self):
        result = audit_media_assets([{"scene_number": 1, "provider": "Archive", "page_url": "https://example.org"}], [1, 2])
        self.assertEqual(result["missing_scenes"], [2])
        self.assertFalse(result["publication_ready"])

    def test_presenter_duration_does_not_claim_lipsync(self):
        result = audit_presenter_timing([{"narration_duration_seconds": 10, "presenter_duration_seconds": 10}])
        self.assertFalse(result["lip_sync_verified"])

    def test_combined_report_can_be_saved(self):
        report = build_workflow_report({}, [], [], [])
        with tempfile.TemporaryDirectory() as folder:
            path = save_workflow_report(report, Path(folder) / "audit.json")
            self.assertTrue(path.is_file())

    def test_combined_workflow_audit_blocks_unreviewed_research(self):
        with tempfile.TemporaryDirectory() as folder:
            script = {
                "sources": [{"source_id": "S1", "title": "Official report",
                             "publisher": "Institution", "published_at": "2025-01-01",
                             "url": "https://example.org/report"}],
                "claims": [{"claim_id": "C1", "text": "A checkable fact",
                            "claim_type": "fact", "source_ids": ["S1"],
                            "evidence": "Official report page 3."}],
            }
            research_path = Path(folder) / "research_verification_report.json"
            write_research_verification_report(script, [], folder)
            report = build_workflow_report(
                {}, [], [], [], research_verification_path=research_path
            )
            self.assertTrue(report["summary"]["publication_blocked"])
            self.assertEqual(report["summary"]["publish_recommendation"],
                             "blocked_by_research_verification")



if __name__ == "__main__":
    unittest.main()
