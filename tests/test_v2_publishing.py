"""Milestone 8: private-only upload, read-back and duplicate prevention."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_v2.config import V2Config
from src.production_v2.models import QualityReport, UploadResult
from src.production_v2.publishing import PrivatePublisher, UploaderAdapter


class FakeUploader:
    def __init__(self, *, privacy="private", publish_at=None, fail=False, channel_id="UC-channel"):
        self.privacy = privacy
        self.publish_at = publish_at
        self.fail = fail
        self.channel_id = channel_id
        self.calls = 0

    def verify_channel(self) -> dict:
        return {"verified": True, "id": self.channel_id, "handle": "@engineeringphysics-n5g"}

    def upload(self, video: Path, title: str, description: str, tags: list,
               *, privacy_status: str, disable_scheduling: bool, marker: str) -> str:
        self.calls += 1
        assert privacy_status == "private"
        assert disable_scheduling is True
        assert marker.startswith("PV2-CONTENT:")
        if self.fail:
            raise RuntimeError("quota exceeded")
        return "VIDEO123"

    def find_private_video_by_marker(self, marker: str) -> str | None:
        return None

    def get_video_status(self, video_id: str) -> dict:
        return {"found": True, "privacy_status": self.privacy, "publish_at": self.publish_at,
                "channel_id": self.channel_id, "title": "t"}


def quality(passed: bool = True) -> QualityReport:
    report = QualityReport()
    if passed:
        for field in QualityReport.FIELD_NAMES:
            setattr(report, field, "PASS")
    return report.finalize()


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"x" * 1000)
        self.config = V2Config.from_env({"PV2_UPLOAD": "1"})

    def tearDown(self):
        self.tmp.cleanup()

    def publisher(self) -> PrivatePublisher:
        return PrivatePublisher(self.root / "ledger.json", self.config)

    def test_quality_rejection_never_calls_uploader(self):
        uploader = FakeUploader()
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(False), uploader=uploader)
        self.assertEqual(result.state, "QUALITY_REJECTED")
        self.assertEqual(uploader.calls, 0)

    def test_disabled_upload_is_skipped(self):
        self.config.upload_enabled = False
        uploader = FakeUploader()
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "SKIPPED")
        self.assertEqual(uploader.calls, 0)

    def test_private_upload_is_verified_and_recorded(self):
        uploader = FakeUploader()
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(), uploader=uploader,
                                          content_hash="abc123def456")
        self.assertEqual(result.state, "UPLOADED_PRIVATE")
        self.assertEqual(result.video_id, "VIDEO123")
        self.assertEqual(result.privacy, "private")
        self.assertTrue(result.uploaded_at)

    def test_wrong_privacy_readback_blocks_success(self):
        uploader = FakeUploader(privacy="public")
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertIn("privacy", result.error)

    def test_scheduled_publish_time_blocks_success(self):
        uploader = FakeUploader(publish_at="2026-11-20T00:00:00Z")
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")

    def test_retry_reuses_marker_and_never_uploads_twice(self):
        uploader = FakeUploader()
        publisher = self.publisher()
        kwargs = dict(video=self.video, metadata={"title": "t"}, quality=quality(),
                      uploader=uploader, content_hash="samehash")
        first = publisher.publish(**kwargs)
        second = publisher.publish(**kwargs)
        self.assertEqual(first.state, "UPLOADED_PRIVATE")
        self.assertEqual(second.state, "UPLOADED_PRIVATE")
        self.assertTrue(second.reused_existing_upload)
        self.assertEqual(uploader.calls, 1)

    def test_unverified_channel_never_uploads(self):
        uploader = FakeUploader()
        uploader.verify_channel = lambda: {"verified": False, "id": "wrong"}
        result = self.publisher().publish(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertEqual(uploader.calls, 0)

    def test_retry_checks_privacy_again_and_keeps_video_id(self):
        uploader = FakeUploader()
        publisher = self.publisher()
        args = dict(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(publisher.publish(**args).state, "UPLOADED_PRIVATE")
        uploader.privacy = "public"
        result = publisher.publish(**args)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertEqual(result.video_id, "VIDEO123")
        self.assertEqual(uploader.calls, 1)

    def test_failed_readback_retries_without_second_upload(self):
        uploader = FakeUploader(privacy="public")
        publisher = self.publisher()
        args = dict(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(publisher.publish(**args).state, "UPLOAD_FAILED")
        uploader.privacy = "private"
        self.assertEqual(publisher.publish(**args).state, "UPLOADED_PRIVATE")
        self.assertEqual(uploader.calls, 1)

    def test_ambiguous_upload_failure_does_not_retry_blindly(self):
        uploader = FakeUploader(fail=True)
        publisher = self.publisher()
        args = dict(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        publisher.publish(**args)
        uploader.fail = False
        result = publisher.publish(**args)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertIn("unknown", result.error)
        self.assertEqual(uploader.calls, 1)

    def test_missing_video_channel_blocks_success(self):
        uploader = FakeUploader()
        uploader.get_video_status = lambda _: {"found": True, "privacy_status": "private"}
        result = self.publisher().publish(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertEqual(result.video_id, "VIDEO123")

    def test_remote_marker_is_reused_and_verified(self):
        uploader = FakeUploader()
        uploader.find_private_video_by_marker = lambda _: "REMOTE123"
        result = self.publisher().publish(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOADED_PRIVATE")
        self.assertEqual(result.video_id, "REMOTE123")
        self.assertTrue(result.reused_existing_upload)
        self.assertEqual(uploader.calls, 0)

    def test_marker_lookup_failure_never_uploads(self):
        uploader = FakeUploader()
        def fail_lookup(_):
            raise RuntimeError("lookup unavailable")
        uploader.find_private_video_by_marker = fail_lookup
        result = self.publisher().publish(video=self.video, metadata={}, quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertEqual(uploader.calls, 0)

    def test_upload_exception_is_recorded_without_hiding_video(self):
        uploader = FakeUploader(fail=True)
        result = self.publisher().publish(video=self.video, metadata={"title": "t"},
                                          quality=quality(), uploader=uploader)
        self.assertEqual(result.state, "UPLOAD_FAILED")
        self.assertTrue(self.video.is_file())
        self.assertTrue((self.root / "last_upload_result.json").is_file())


if __name__ == "__main__":
    unittest.main()
