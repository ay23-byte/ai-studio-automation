import json
import tempfile
import unittest
from pathlib import Path

from core.research_verification import (
    append_source_citations_to_description, assert_research_review_ready,
    audit_research_reviews, build_research_verification_report, load_research_reviews,
    save_claim_review, save_coverage_review, save_source_review,
    write_research_verification_report,
)


class ResearchVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = {
            "sources": [{
                "source_id": "S1", "title": "Official annual report",
                "publisher": "Example Institution", "published_at": "2025-06-01",
                "url": "https://example.org/report",
            }],
            "claims": [{
                "claim_id": "C1", "text": "The programme began in 2020.",
                "claim_type": "fact", "source_ids": ["S1"],
                "evidence": "Annual report, page 4, programme launch date.",
            }],
        }
        self.discovery = [{"url": "https://example.org/report", "title": "Report"}]

    def _write(self):
        return write_research_verification_report(self.script, self.discovery, self.root)

    def test_linked_claim_remains_pending_human_review(self):
        report = build_research_verification_report(self.script, self.discovery)
        self.assertEqual(report["claims"][0]["traceability_status"],
                         "source_linked_pending_human_review")
        self.assertFalse(report["fact_check_complete"])

    def test_generated_description_contains_source_citation_links(self):
        script = dict(self.script, description="A documentary overview.")
        append_source_citations_to_description(script)
        self.assertIn("Sources & further reading", script["description"])
        self.assertIn("https://example.org/report", script["description"])
        self.assertIn("\n\nSources", script["description"])

    def test_claim_without_evidence_or_known_source_is_not_traceable(self):
        script = {
            "sources": [{"source_id": "S1", "title": "A", "publisher": "B",
                         "url": "https://example.org"}],
            "claims": [{"claim_id": "C1", "text": "An assertion",
                        "source_ids": ["S99"], "evidence": ""}],
        }
        report = build_research_verification_report(script, [])
        self.assertEqual(report["claims"][0]["traceability_status"], "needs_verification")
        self.assertTrue(report["issues"])

    def test_source_not_in_discovery_is_flagged_for_manual_validation(self):
        report = build_research_verification_report(self.script, [])
        self.assertFalse(report["sources"][0]["matched_discovery_lead"])
        self.assertEqual(report["sources"][0]["verification_state"],
                         "requires_manual_source_validation")

    def test_publication_gate_requires_source_claim_and_coverage_reviews(self):
        report = self._write()
        store = load_research_reviews(self.root, report)
        self.assertFalse(audit_research_reviews(report, store)["publication_ready"])
        save_source_review(self.root, report, "S1", "reviewed",
                           "Opened original report and checked page 4.")
        save_claim_review(self.root, report, "C1", "verified",
                          "Page 4 explicitly supports the programme start date.")
        save_coverage_review(self.root, report, True,
                             "Checked the full narration and found no uncited factual claims.")
        self.assertTrue(assert_research_review_ready(self.root)["publication_ready"])

    def test_disputed_claim_blocks_publication(self):
        report = self._write()
        save_source_review(self.root, report, "S1", "reviewed",
                           "Opened original report and checked page 4.")
        save_claim_review(self.root, report, "C1", "disputed",
                          "Source date conflicts with the narration.")
        save_coverage_review(self.root, report, True,
                             "Reviewed every chapter and the claim ledger.")
        with self.assertRaisesRegex(ValueError, "Claim review status is 'disputed'"):
            assert_research_review_ready(self.root)

    def test_verified_claim_requires_linked_source_and_evidence(self):
        report = build_research_verification_report({
            "sources": [{"source_id": "S1", "title": "Report", "publisher": "Publisher",
                         "url": "https://example.org"}],
            "claims": [{"claim_id": "C1", "text": "A fact", "source_ids": [], "evidence": ""}],
        }, [])
        with self.assertRaisesRegex(ValueError, "cannot be marked verified"):
            save_claim_review(self.root, report, "C1", "verified", "I checked the source.")

    def test_missing_claim_ledger_blocks_publication(self):
        report = self._write()
        report["claims"] = []
        path = self.root / "research_verification_report.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "structured claim ledger"):
            assert_research_review_ready(self.root)

    def test_changed_claim_resets_previous_human_decision(self):
        report = self._write()
        save_source_review(self.root, report, "S1", "reviewed", "Opened the official report.")
        save_claim_review(self.root, report, "C1", "verified", "Checked the evidence on page four.")
        changed = dict(self.script)
        changed["claims"] = [dict(self.script["claims"][0], text="The programme began in 2021.")]
        changed_report = build_research_verification_report(changed, self.discovery)
        store = load_research_reviews(self.root, changed_report)
        self.assertEqual(store["claims"]["C1"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
