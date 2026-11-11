import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.upload_documentary_artifact import _find_video, _validate_release_evidence


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


    def test_find_video_probes_single_packaged_mp4_without_external_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "output" / "longform_1.mp4"
            video.parent.mkdir()
            video.write_bytes(b"x" * 100_001)
            expected = {"duration_seconds": 600.0, "width": 1920, "height": 1080}
            with patch("core.release_certification.probe_release_video", return_value=expected) as probe:
                found_path, metadata = _find_video(root)
            self.assertEqual(found_path, video)
            self.assertEqual(metadata, expected)
            probe.assert_called_once_with(video)

    def test_find_video_ignores_intermediate_build_clips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final_video = root / "output" / "longform_1.mp4"
            final_video.parent.mkdir()
            final_video.write_bytes(b"x" * 100_001)
            for index in range(45):
                clip = root / "build" / "job_1" / f"clip_{index}.mp4"
                clip.parent.mkdir(parents=True, exist_ok=True)
                clip.write_bytes(b"x" * 100_001)
            expected = {"duration_seconds": 600.0, "width": 1920, "height": 1080}
            with patch("core.release_certification.probe_release_video", return_value=expected) as probe:
                found_path, metadata = _find_video(root)
            self.assertEqual(found_path, final_video)
            self.assertEqual(metadata, expected)
            probe.assert_called_once_with(final_video)

    def test_find_video_refuses_multiple_final_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            (output / "one.mp4").write_bytes(b"x" * 100_001)
            (output / "two.mp4").write_bytes(b"x" * 100_001)
            with self.assertRaisesRegex(ValueError, "found 2"):
                _find_video(root)

    def test_find_video_refuses_missing_final_export_even_if_clips_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / "build" / "job_1" / "chapter.mp4"
            clip.parent.mkdir(parents=True)
            clip.write_bytes(b"x" * 100_001)
            with self.assertRaisesRegex(ValueError, "found 0"):
                _find_video(root)


if __name__ == "__main__":
    unittest.main()