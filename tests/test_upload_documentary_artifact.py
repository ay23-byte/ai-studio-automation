import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.upload_documentary_artifact import _validate_release_evidence


class DocumentaryUploadEvidenceTests(unittest.TestCase):
    def _build(self, root):
        payloads = {
            "visual_review_report.json": {"shots": [{"shot_id": "shot_0001"}], "issues": []},
            "visual_consistency_report.json": {"status": "PASS", "summary": {"fail_count": 0}},
            "audio_quality_report.json": {"status": "PASS"},
            "research_verification_report.json": {"claim_count": 1, "source_count": 1},
            "research_review_decisions.json": {"coverage_reviewed": True},
            "quality_report.json": {"status": "PASS"},
        }
        for name, value in payloads.items():
            (root / name).write_text(json.dumps(value), encoding="utf-8")
        with (root / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["shot_id", "use_status"])
            writer.writeheader()
            writer.writerow({"shot_id": "shot_0001", "use_status": "eligible_pending_human_review"})

    def test_complete_release_evidence_passes_technical_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            report = _validate_release_evidence(root)
            self.assertEqual(report["shot_count"], 1)
            self.assertEqual(report["licence_rows"], 1)

    def test_missing_release_report_blocks_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            (root / "audio_quality_report.json").unlink()
            with self.assertRaisesRegex(ValueError, "missing: audio_quality_report.json"):
                _validate_release_evidence(root)

    def test_blocked_asset_blocks_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            with (root / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["shot_id", "use_status"])
                writer.writeheader()
                writer.writerow({"shot_id": "shot_0001", "use_status": "blocked"})
            with self.assertRaisesRegex(ValueError, "blocked assets"):
                _validate_release_evidence(root)


if __name__ == "__main__":
    unittest.main()