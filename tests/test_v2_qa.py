"""Milestone 8/9: structured seven-field quality decision and upload gate."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_v2.artifacts import ArtifactStore
from src.production_v2.qa import build_report, copyright
from src.production_v2.licenses import AssetLibrary, AssetRecord
from src.production_v2.models import QualityReport


def parts(pass_all: bool = True) -> dict:
    value = "PASS" if pass_all else "FAIL"
    issue = [] if pass_all else ["demo issue"]
    return {name: {"status": value, "issues": issue, "warnings": [], "evidence": {}}
            for name in ("technical", "visual", "audio", "subtitles", "research", "content", "copyright")}


class QATests(unittest.TestCase):
    def test_quality_report_has_exactly_the_required_seven_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp), "qa-run")
            report = build_report(parts(), store, content_type="news_short", run_id="qa-run")
            self.assertEqual(set(report.decision()), set(QualityReport.FIELD_NAMES))
            self.assertTrue(all(v == "PASS" for v in report.decision().values()))
            self.assertTrue((store.path("quality_decision.json")).is_file())

    def test_any_failed_critical_check_blocks_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp), "qa-run")
            data = parts()
            data["research"]["status"] = "FAIL"
            data["research"]["issues"] = ["unsupported claim"]
            report = build_report(data, store, content_type="documentary", run_id="qa-run")
            self.assertEqual(report.research_quality, "FAIL")
            self.assertEqual(report.upload_eligibility, "FAIL")
            self.assertTrue(any("unsupported claim" in issue for issue in report.issues))

    def test_copyright_gate_fails_on_incomplete_evidence(self):
        library = AssetLibrary()
        library.add(AssetRecord("a", "https://example.org", "X", "CC BY 4.0", sha256="abc"))
        result = copyright(library)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("incomplete" in issue for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()
