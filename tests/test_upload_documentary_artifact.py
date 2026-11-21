import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.upload_documentary_artifact import _find_video, _validate_release_evidence, upload_automated_artifact


class DocumentaryUploadEvidenceTests(unittest.TestCase):
    def _build(self, root):
        media = root / "shot_0001.mp4"
        media.write_bytes(b"test media fixture")
        payloads = {
            "visual_review_report.json": {"semantic_screening_enabled": True,
                "shots": [{"shot_id": "shot_0001", "media_path": str(media),
                           "semantic_screening": {"status": "match", "match_score": 0.91}}],
                "issues": []},
            "visual_consistency_report.json": {"status": "PASS", "summary": {"fail_count": 0}},
            "audio_quality_report.json": {"status": "PASS"},
            "research_verification_report.json": {"claim_count": 1, "source_count": 1, "sources": [{"source_id": "S1", "url": "https://example.org/source", "url_valid": True, "metadata_complete": True}], "claims": [{"claim_id": "C1", "traceability_status": "source_linked_pending_human_review", "valid_source_ids": ["S1"], "evidence": "Evidence note."}]},
            "research_review_decisions.json": {"coverage_reviewed": True},
            "quality_report.json": {"status": "PASS"},
        }
        for name, value in payloads.items():
            (root / name).write_text(json.dumps(value), encoding="utf-8")
        from core.editorial_review import load_reviews, save_review
        report_path = root / "visual_review_report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        load_reviews(report_path, report["shots"])
        save_review(
            report_path, "shot_0001", status="approved",
            reviewer_notes="Inspected the actual shot and confirmed it matches the narration.",
            source_review_status="reviewed", rights_review_status="reviewed",
            rights_notes="Verified the supplied CC0 source and its reuse terms.",
            licence_name="CC0", attribution_text="Test credit.",
        )
        (root / "credits.txt").write_text("Documentary source credits\\nTest credit\\n", encoding="utf-8")
        with (root / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["shot_id", "use_status", "fallback_required", "licence", "attribution_text"])
            writer.writeheader()
            writer.writerow({"shot_id": "shot_0001", "use_status": "eligible_pending_human_review", "fallback_required": "false", "licence": "CC0", "attribution_text": "Test credit."})

    def test_complete_release_evidence_passes_technical_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            with patch("core.editorial_review.assert_visual_review_ready", return_value={"publication_ready": True}):
                report = _validate_release_evidence(root)
            self.assertEqual(report["shot_count"], 1)
            self.assertEqual(report["licence_rows"], 1)

    def test_machine_only_release_uses_semantic_screening_without_human_attestations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            from core.editorial_review import review_store_path
            review_store_path(root / "visual_review_report.json").unlink(missing_ok=True)
            (root / "research_review_decisions.json").unlink(missing_ok=True)
            report = _validate_release_evidence(root, machine_only=True)
            self.assertEqual(report["release_mode"], "machine_only")
            self.assertEqual(report["shot_count"], 1)

    def test_machine_only_release_honours_explicit_upload_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            quality_path = root / "quality_report.json"
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality["upload_blocked"] = True
            quality_path.write_text(json.dumps(quality), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicitly blocks upload"):
                _validate_release_evidence(root, machine_only=True)

    def test_machine_only_release_blocks_unmatched_visuals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._build(root)
            visual_path = root / "visual_review_report.json"
            visual = json.loads(visual_path.read_text(encoding="utf-8"))
            visual["shots"][0]["semantic_screening"] = {"status": "uncertain", "match_score": 0.4}
            visual_path.write_text(json.dumps(visual), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "did not confirm every shot"):
                _validate_release_evidence(root, machine_only=True)

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
                writer = csv.DictWriter(handle, fieldnames=["shot_id", "use_status", "fallback_required", "licence", "attribution_text"])
                writer.writeheader()
                writer.writerow({"shot_id": "shot_0001", "use_status": "blocked", "fallback_required": "true", "licence": "", "attribution_text": ""})
            with patch("core.editorial_review.assert_visual_review_ready", return_value={"publication_ready": True}):
                with self.assertRaisesRegex(ValueError, "did not clear these assets"):
                    _validate_release_evidence(root)


    def test_find_video_probes_single_packaged_mp4_without_external_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "output" / "longform_1.mp4"
            video.parent.mkdir()
            video.write_bytes(b"x" * 100_001)
            expected = {"duration_seconds": 600.0, "width": 1920, "height": 1080}
            with patch("core.release_certification.probe_release_video", return_value=expected) as probe:
                found_path, metadata = _find_video(root, root / "build" / "job_1")
            self.assertEqual(found_path, video)
            self.assertEqual(metadata, expected)
            probe.assert_called_once_with(video)

    def test_find_video_ignores_intermediate_build_clips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final_video = root / "output" / "longform_1.mp4"
            final_video.parent.mkdir()
            final_video.write_bytes(b"x" * 100_001)
            for index in range(28):
                (final_video.parent / f"preview_{index}.mp4").write_bytes(b"x" * 100_001)
            for index in range(45):
                clip = root / "build" / "job_1" / f"clip_{index}.mp4"
                clip.parent.mkdir(parents=True, exist_ok=True)
                clip.write_bytes(b"x" * 100_001)
            expected = {"duration_seconds": 600.0, "width": 1920, "height": 1080}
            with patch("core.release_certification.probe_release_video", return_value=expected) as probe:
                found_path, metadata = _find_video(root, root / "build" / "job_1")
            self.assertEqual(found_path, final_video)
            self.assertEqual(metadata, expected)
            probe.assert_called_once_with(final_video)

    def test_find_video_selects_job_matched_final_export_among_other_mp4s(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            (output / "longform_1.mp4").write_bytes(b"x" * 100_001)
            (output / "longform_2.mp4").write_bytes(b"x" * 100_001)
            (output / "preview.mp4").write_bytes(b"x" * 100_001)
            expected = {"duration_seconds": 600.0, "width": 1920, "height": 1080}
            with patch("core.release_certification.probe_release_video", return_value=expected) as probe:
                found_path, metadata = _find_video(root, root / "build" / "job_1")
            self.assertEqual(found_path, output / "longform_1.mp4")
            self.assertEqual(metadata, expected)
            probe.assert_called_once_with(found_path)

    def test_find_video_does_not_select_another_jobs_final_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            (output / "longform_1.mp4").write_bytes(b"x" * 100_001)
            with self.assertRaisesRegex(ValueError, "longform_2.mp4"):
                _find_video(root, root / "build" / "job_2")

    def test_find_video_refuses_missing_final_export_even_if_clips_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            (output / "preview.mp4").write_bytes(b"x" * 100_001)
            with self.assertRaisesRegex(ValueError, "longform_1.mp4"):
                _find_video(root, root / "build" / "job_1")

    def test_automatic_documentary_upload_is_private_and_unscheduled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build_dir = root / "build" / "job_42"
            build_dir.mkdir(parents=True)
            video = root / "output" / "longform_42.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"documentary mp4 fixture")
            metadata_path = build_dir / "release_metadata.json"
            metadata_path.write_text("{}", encoding="utf-8")
            metadata = {"title": "Test Documentary", "description": "Test description", "tags": ["test"]}
            probe = {"duration_seconds": 600, "width": 1920, "height": 1080}
            channel = {"verified": True, "id": "channel-123", "title": "Engineering Physics"}
            stored = {"found": True, "privacy_status": "private", "publish_at": None, "channel_id": "channel-123"}
            with patch("scripts.upload_documentary_artifact._find_release_metadata", return_value=(metadata_path, metadata)), \
                 patch("scripts.upload_documentary_artifact._find_video", return_value=(video, probe)), \
                 patch("scripts.upload_documentary_artifact._validate_release_evidence", return_value={"release_mode": "machine_only"}), \
                 patch("core.uploader.verify_channel", return_value=channel), \
                 patch("scripts.upload_documentary_artifact.find_private_video_by_marker", return_value=None), \
                 patch("scripts.upload_documentary_artifact.upload", return_value="video-456") as upload_mock, \
                 patch("scripts.upload_documentary_artifact.wait_processed", return_value="ok"), \
                 patch("core.uploader.get_video_status", return_value=stored):
                result = upload_automated_artifact(root, "42", machine_only=True)
            upload_mock.assert_called_once()
            self.assertEqual(upload_mock.call_args.kwargs["privacy_status"], "private")
            self.assertTrue(upload_mock.call_args.kwargs["disable_scheduling"])
            self.assertTrue(result["privacy_verified"])
            self.assertEqual(result["privacy_status"], "private")
            self.assertIsNone(result["publish_at"])
            saved = json.loads((root / "youtube-upload-result.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["privacy_verified"])

    def test_automatic_documentary_upload_fails_if_youtube_reports_public(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build_dir = root / "build" / "job_42"
            build_dir.mkdir(parents=True)
            video = root / "output" / "longform_42.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"documentary mp4 fixture")
            metadata_path = build_dir / "release_metadata.json"
            metadata_path.write_text("{}", encoding="utf-8")
            metadata = {"title": "Test Documentary", "description": "Test description", "tags": ["test"]}
            probe = {"duration_seconds": 600, "width": 1920, "height": 1080}
            channel = {"verified": True, "id": "channel-123", "title": "Engineering Physics"}
            stored = {"found": True, "privacy_status": "public", "publish_at": None, "channel_id": "channel-123"}
            with patch("scripts.upload_documentary_artifact._find_release_metadata", return_value=(metadata_path, metadata)), \
                 patch("scripts.upload_documentary_artifact._find_video", return_value=(video, probe)), \
                 patch("scripts.upload_documentary_artifact._validate_release_evidence", return_value={"release_mode": "machine_only"}), \
                 patch("core.uploader.verify_channel", return_value=channel), \
                 patch("scripts.upload_documentary_artifact.find_private_video_by_marker", return_value=None), \
                 patch("scripts.upload_documentary_artifact.upload", return_value="video-456") as upload_mock, \
                 patch("scripts.upload_documentary_artifact.wait_processed", return_value="ok"), \
                 patch("core.uploader.get_video_status", return_value=stored):
                with self.assertRaisesRegex(RuntimeError, "Private upload could not be verified"):
                    upload_automated_artifact(root, "42", machine_only=True)
            upload_mock.assert_called_once()
            self.assertEqual(upload_mock.call_args.kwargs["privacy_status"], "private")
            self.assertTrue(upload_mock.call_args.kwargs["disable_scheduling"])
            failure = json.loads((root / "youtube-upload-result.json").read_text(encoding="utf-8"))
            self.assertFalse(failure["privacy_verified"])
            self.assertIn("not 'private'", failure["verification_problems"][0])


if __name__ == "__main__":
    unittest.main()