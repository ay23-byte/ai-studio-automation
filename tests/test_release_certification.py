import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.editorial_review import load_reviews, save_review
from core.release_certification import (
    assert_release_certified, certify_documentary,
)
from core.research_verification import (
    save_claim_review, save_coverage_review, save_source_review,
    write_research_verification_report,
)


class ReleaseCertificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "final.mp4"
        self.video.write_bytes(b"x" * 150_000)
        self._write_json("visual_review_report.json", {
            "shot_count": 1, "semantic_screening_enabled": False,
            "shots": [{
                "shot_id": "shot_0001", "chapter_id": "chapter_1",
                "media_path": str(self.video), "media_type": "video",
                "narration": "A ship approaches the port.",
                "visual_prompt": "A cargo ship approaching a port.",
                "visual_quality": {"status": "pass"},
            }],
        })
        self._write_json("visual_consistency_report.json", {
            "status": "PASS",
            "summary": {"fail_count": 0, "review_count": 0, "unavailable_count": 0},
            "shots": [],
        })
        self._write_json("audio_quality_report.json", {
            "status": "PASS", "human_review_status": "reviewed",
            "metrics": {"integrated_lufs": -16.0, "true_peak_dbfs": -1.5},
        })
        self._write_json("quality_report.json", {"status": "PASS", "score": 98})
        self._write_json("release_metadata.json", {
            "title": "Sample documentary", "description": "Sources & further reading: https://example.org/port-report",
            "tags": ["documentary"],
        })
        self._prepare_visual_approval()
        self._prepare_research_approval()

    def _write_json(self, name, value):
        (self.root / name).write_text(json.dumps(value, indent=2), encoding="utf-8")

    def _prepare_visual_approval(self):
        report = json.loads((self.root / "visual_review_report.json").read_text(encoding="utf-8"))
        load_reviews(self.root / "visual_review_report.json", report["shots"])
        save_review(
            self.root / "visual_review_report.json", "shot_0001",
            status="approved", reviewer_notes="Shot matches narration.",
            source_review_status="reviewed",
            rights_review_status="reviewed",
            rights_notes="Checked asset terms and permission.",
            licence_name="Original asset / permission confirmed",
            attribution_text="No attribution required; original asset.",
        )

    def _prepare_research_approval(self):
        script = {
            "sources": [{
                "source_id": "S1", "title": "Port authority report",
                "publisher": "Port Authority", "published_at": "2025-01-01",
                "url": "https://example.org/port-report",
            }],
            "claims": [{
                "claim_id": "C1", "text": "The port opened in 1980.",
                "claim_type": "fact", "source_ids": ["S1"],
                "evidence": "Official report, history section.",
            }],
        }
        report = write_research_verification_report(script, [], self.root)
        save_source_review(self.root, report, "S1", "reviewed",
                           "Opened official report and inspected history section.")
        save_claim_review(self.root, report, "C1", "verified",
                          "Source history section supports the opening year.")
        save_coverage_review(self.root, report, True,
                             "Checked the full narration for uncited factual claims.")

    def _valid_video_metadata(self):
        return {
            "path": str(self.video.resolve()), "size_bytes": self.video.stat().st_size,
            "duration_seconds": 300.0, "width": 1920, "height": 1080,
            "aspect_ratio": 1.7778, "frame_rate": 30.0,
            "video_codec": "h264", "audio_codec": "aac",
            "audio_sample_rate": 48000, "audio_channels": 2,
        }

    @patch("core.release_certification.probe_release_video")
    def test_complete_build_is_certified_and_hash_locked(self, probe):
        probe.return_value = self._valid_video_metadata()
        certificate = certify_documentary(self.root, self.video)
        self.assertEqual(certificate["status"], "CERTIFIED")
        self.assertTrue((self.root / "release_certificate.json").is_file())
        self.assertEqual(assert_release_certified(self.root, self.video)["status"], "CERTIFIED")

    @patch("core.release_certification.probe_release_video")
    def test_changed_video_invalidates_certificate(self, probe):
        probe.return_value = self._valid_video_metadata()
        certificate = certify_documentary(self.root, self.video)
        self.assertEqual(certificate["status"], "CERTIFIED")
        self.video.write_bytes(b"changed video bytes" * 9000)
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            assert_release_certified(self.root, self.video)

    @patch("core.release_certification.probe_release_video")
    def test_pending_rights_review_blocks_release_certificate(self, probe):
        probe.return_value = self._valid_video_metadata()
        review = json.loads((self.root / "visual_review_report.json").read_text(encoding="utf-8"))
        load_reviews(self.root / "visual_review_report.json", review["shots"])
        save_review(
            self.root / "visual_review_report.json", "shot_0001",
            rights_review_status="pending",
        )
        certificate = certify_documentary(self.root, self.video)
        self.assertEqual(certificate["status"], "BLOCKED")
        self.assertTrue(any("Rights review status" in item for item in certificate["blockers"]))

    @patch("core.release_certification.probe_release_video")
    def test_bad_final_video_profile_blocks_release(self, probe):
        probe.side_effect = ValueError("Final export is below 720p landscape delivery.")
        certificate = certify_documentary(self.root, self.video)
        self.assertEqual(certificate["status"], "BLOCKED")
        self.assertTrue(any("below 720p" in item for item in certificate["blockers"]))

    @patch("core.release_certification.probe_release_video")
    def test_missing_source_citation_in_release_description_blocks(self, probe):
        probe.return_value = self._valid_video_metadata()
        self._write_json("release_metadata.json", {
            "title": "Sample documentary", "description": "No source links included.",
            "tags": ["documentary"],
        })
        certificate = certify_documentary(self.root, self.video)
        self.assertEqual(certificate["status"], "BLOCKED")
        self.assertTrue(any("missing from the release description" in item
                            for item in certificate["blockers"]))

    def test_release_assertion_requires_certificate(self):
        with self.assertRaisesRegex(ValueError, "Release certificate is missing"):
            assert_release_certified(self.root, self.video)


if __name__ == "__main__":
    unittest.main()
