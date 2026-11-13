"""Documentary dossier -> shared review package (schema v2)."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from scripts.package_longform_review import create_package, main
from tests.review_package_fixture import approve, fake_media


class LongformReviewPackageTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def _fixture(self, *, consistency=None, licence="CC BY 4.0", use_status="eligible_pending_human_review",
                 screening="mismatch", research=True, audio="PASS"):
        build = self.root / "build" / "job_42"
        build.mkdir(parents=True)
        media = build / "shot.mp4"
        media.write_bytes(b"media" * 100)
        (build / "subs.ass").write_text("[Script Info]\n", encoding="utf-8")
        (build / "credits.txt").write_text("DOCUMENTARY SOURCE CREDITS\nTest credit, CC BY 4.0\n", encoding="utf-8")
        (build / "script.json").write_text(json.dumps({"title": "Test documentary", "description": "About a test."}),
                                           encoding="utf-8")
        (build / "release_metadata.json").write_text(json.dumps(
            {"title": "Test documentary", "description": "About a test.", "tags": ["history"]}), encoding="utf-8")
        (build / "visual_review_report.json").write_text(json.dumps({
            "shots": [{
                "shot_id": "shot_0001", "media_path": str(media), "media_type": "video",
                "narration": "A verified test narration.", "visual_prompt": "A test visual",
                "start_seconds": 0.0, "duration_seconds": 6.0, "sample_frames": [],
                "source": {"provider": "Test Archive", "page_url": "https://example.org/item/1",
                           "title": "Item one", "creator": "A. Maker"},
                "semantic_screening": {"status": screening, "match_score": 0.12, "reason": "Different subject.",
                                       "recommendation": "Replace with a scene matching the narration."},
            }],
            "semantic_screening_enabled": True, "issues": [],
        }), encoding="utf-8")
        fields = ["shot_id", "source", "url", "author", "licence", "attribution_text", "licence_url", "use_status",
                  "fallback_required"]
        with (build / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({"shot_id": "shot_0001", "source": "Test Archive", "url": "https://example.org/item/1",
                             "author": "A. Maker", "licence": licence, "attribution_text": "Test credit",
                             "licence_url": "https://creativecommons.org/licenses/by/4.0/",
                             "use_status": use_status, "fallback_required": "False"})
        reports = {
            "visual_consistency_report.json": consistency or {
                "status": "PASS", "summary": {"fail_count": 0, "review_count": 0, "unavailable_count": 0}},
            "quality_report.json": {"status": "PASS"},
        }
        if audio:
            reports["audio_quality_report.json"] = {"status": audio}
        if research:
            reports["research_verification_report.json"] = {
                "source_count": 1, "claim_count": 1,
                "sources": [{"source_id": "S1", "title": "A paper", "publisher": "Journal",
                             "url": "https://example.org/paper", "url_valid": True, "metadata_complete": True}],
                "claims": [{"claim_id": "C1", "text": "The test happened in 1969.", "evidence": "Page 2.",
                            "valid_source_ids": ["S1"], "traceability_status": "source_linked_pending_human_review"}]}
        for name, data in reports.items():
            (build / name).write_text(json.dumps(data), encoding="utf-8")
        video = self.root / "output" / "longform_42.mp4"
        video.parent.mkdir()
        video.write_bytes(b"video" * 30000)
        return build, video

    def package(self, **kwargs):
        build, video = self._fixture(**kwargs)
        with fake_media("longform"):
            root = create_package(build, video, pipeline_job_id=42)
        return root, json.loads((root / "production_manifest.json").read_text()), \
            json.loads((root / "package_summary.json").read_text())

    def qa(self, root, name):
        return json.loads((root / "qa" / f"{name}.json").read_text())

    def test_creates_review_required_package_without_approving_anything(self):
        root, manifest, summary = self.package()
        self.assertEqual(summary["status"], "REVIEW_REQUIRED", summary["errors"])
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["mode"], "longform")
        self.assertIn("-j42", manifest["job_id"])
        scene = manifest["scenes"][0]
        asset = scene["assets"][0]
        self.assertEqual(asset["provenance"]["origin"], "source")
        self.assertEqual(asset["provenance"]["source_url"], "https://example.org/item/1")
        self.assertEqual(asset["rights"]["status"], "unreviewed")
        self.assertEqual(asset["rights"]["license"], "CC BY 4.0")
        self.assertEqual(asset["rights"]["attribution"], "Test credit")
        self.assertEqual(scene["screening"]["status"], "mismatch")
        self.assertEqual(scene["start_seconds"], 0.0)
        self.assertEqual(manifest["research"]["claims"][0]["claim_id"], "C1")
        self.assertEqual(manifest["publish"]["title"], "Test documentary")
        for name in ("video.mp4", "captions.ass", "review_dashboard.html", "review_decisions_template.json",
                     "evidence/licenses.csv", "evidence/credits.txt"):
            self.assertTrue((root / name).is_file(), name)
        self.assertEqual(json.loads((root / "review_checklist.json").read_text())["human_review_status"], "pending")
        semantic = self.qa(root, "semantic")
        self.assertEqual(semantic["status"], "REVIEW")           # a mismatch is evidence for the human, not a pass
        self.assertEqual(semantic["screening_counts"]["mismatch"], 1)
        self.assertEqual(self.qa(root, "audio")["status"], "REVIEW")   # metering PASS never approves the audio

    def test_package_can_be_approved_and_released_only_with_claim_and_source_reviews(self):
        root, manifest, _ = self.package()
        decisions = approve(root)
        with fake_media("longform"):
            self.assertTrue(contract.evaluate_release(root, decisions, expected_mode="longform")["allowed"])
            decisions["claim_reviews"][0]["decision"] = "pending"
            report = contract.evaluate_release(root, decisions)
        self.assertFalse(report["allowed"])
        self.assertIn("claim C1", "\n".join(report["blocking"]))

    def test_inherited_documentary_gates_still_block(self):
        cases = (
            ({"consistency": {"status": "FAIL", "summary": {"fail_count": 1, "review_count": 0},
                              "shots": [{"shot_id": "shot_0001", "status": "fail", "issues": ["below 640x360"]}]}},
             "technical", "Visual consistency report is 'FAIL'"),
            ({"consistency": {"status": "PASS", "summary": {"fail_count": 0, "review_count": 2}}},
             "technical", "review 2"),
            ({"use_status": "blocked_unclear_licence"}, "assets", "licence register status 'blocked_unclear_licence'"),
            ({"research": False}, "semantic", "no research_verification_report.json"),
            ({"audio": "FAIL"}, "audio", "FAIL"),
            ({"audio": None}, "audio", "No automated audio metering report"),
        )
        for index, (kwargs, report_name, fragment) in enumerate(cases):
            with self.subTest(case=fragment):
                self.root = Path(self._temp.name) / f"case{index}"
                self.root.mkdir()
                root, _, summary = self.package(**kwargs)
                self.assertEqual(summary["status"], "FAIL")
                report = self.qa(root, report_name)
                self.assertEqual(report["status"], "FAIL")
                self.assertIn(fragment, " ".join(str(item) for item in report["details"]))

    def test_unaccepted_licence_keeps_the_package_unreleasable(self):
        root, _, summary = self.package(licence="All rights reserved")
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        with fake_media("longform"):
            report = contract.evaluate_release(root, approve(root))
        self.assertFalse(report["allowed"])
        self.assertIn("is unclear or not accepted", "\n".join(report["blocking"]))

    def test_missing_visual_media_fails_closed(self):
        build, video = self._fixture()
        report = json.loads((build / "visual_review_report.json").read_text())
        report["shots"][0]["media_path"] = str(build / "missing.mp4")
        (build / "visual_review_report.json").write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(FileNotFoundError, "media for shot_0001"):
            create_package(build, video)

    def test_cli_exit_codes_distinguish_review_from_unapprovable(self):
        build, video = self._fixture()
        # stdout is captured: an "::error::" line printed by a passing test would
        # otherwise appear as a failure annotation on the Actions run.
        with fake_media("longform"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--build-dir", str(build), "--video", str(video)]), 0)
            (build / "quality_report.json").write_text(json.dumps({"status": "FAIL"}))
            self.assertEqual(main(["--build-dir", str(build), "--video", str(video)]), 2)
            self.assertEqual(main(["--build-dir", str(build / "nope"), "--video", str(video)]), 1)


if __name__ == "__main__":
    unittest.main()
