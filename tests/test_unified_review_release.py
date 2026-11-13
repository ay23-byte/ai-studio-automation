"""One release path for all three modes: what uploads, what is refused, what is verified."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import review_contract as contract
from scripts.apply_review_decisions import DecisionsRejected, apply_decisions
from scripts.release_review_package import ReleaseBlocked, ReleaseUnverified, release_package
from tests.review_package_fixture import FakeUploader, approve, fake_media, make_package, source_asset


class _Release(unittest.TestCase):
    mode = "shortform"

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "package"
        self.root.mkdir()
        self.reports = Path(self._temp.name) / "reports"
        self.manifest = make_package(self.root, self.mode)
        self.sha = self.manifest["video"]["sha256"]
        self.decisions = approve(self.root)

    def release(self, uploader=None, **overrides):
        uploader = uploader if uploader is not None else FakeUploader()
        self.uploader = uploader
        kwargs = dict(expected_mode=self.mode, source_run_id="4242", report_dir=self.reports,
                      uploader=uploader, processing_wait_seconds=0)
        confirm = overrides.pop("confirm", True)
        decisions = overrides.pop("decisions", self.decisions)
        sha = overrides.pop("sha", self.sha)
        kwargs.update(overrides)
        with fake_media(self.mode):
            return release_package(self.root, decisions, sha, confirm, **kwargs)

    def report(self):
        return json.loads((self.reports / "release_report.json").read_text())

    def assertRefused(self, fragment, **overrides):
        uploader = overrides.pop("uploader", None) or FakeUploader()
        with self.assertRaises(ReleaseBlocked) as caught:
            self.release(uploader=uploader, **overrides)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(uploader.uploads, [], "nothing may be uploaded when a release is refused")
        report = self.report()
        self.assertEqual(report["outcome"], "blocked")
        self.assertIn(fragment, report["refusal"])
        return report


class ApprovedReleaseTests(_Release):
    def test_uploads_the_exact_approved_file_private_and_reads_privacy_back(self):
        report = self.release()
        self.assertEqual(report["outcome"], "uploaded_private_verified")
        self.assertEqual(len(self.uploader.uploads), 1)
        call = self.uploader.uploads[0]
        self.assertEqual(contract.sha256_file(call["video_path"]), self.sha)
        self.assertEqual(call["privacy_status"], "private")
        self.assertIs(call["disable_scheduling"], True)
        self.assertIn("AI-STUDIO-REVIEWED-SHA256:" + self.sha, call["description"])
        self.assertEqual(call["title"], "Test title")
        upload = report["upload"]
        self.assertEqual(upload["privacy_status_read_back"], "private")
        self.assertEqual(upload["channel_id_read_back"], "UC_TEST")
        self.assertEqual(upload["video_id"], "vid_new")
        self.assertIs(upload["reused_existing_upload"], False)
        stored = self.report()
        self.assertEqual(stored["job_id"], self.manifest["job_id"])
        self.assertEqual(stored["reviewer"], "Reviewer A")
        self.assertEqual(stored["request"]["source_run_id"], "4242")
        # Every result file is valid JSON (the earlier releaser appended a literal "\\n").
        for name in ("release_report.json", "youtube-upload-result.json"):
            json.loads((self.reports / name).read_text())
        self.assertTrue((self.reports / "release_report.md").read_text().startswith("## Release evaluation: ALLOWED"))

    def test_all_three_modes_use_this_one_path(self):
        for mode in contract.MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "package"
                root.mkdir()
                manifest = make_package(root, mode)
                uploader = FakeUploader()
                with fake_media(mode):
                    report = release_package(root, approve(root), manifest["video"]["sha256"], True,
                                             expected_mode=mode, source_run_id="4242", uploader=uploader,
                                             processing_wait_seconds=0)
                self.assertEqual(report["outcome"], "uploaded_private_verified")
                self.assertEqual(len(uploader.uploads), 1)

    def test_running_the_release_twice_never_uploads_twice(self):
        uploader = FakeUploader()
        self.release(uploader=uploader)
        second = self.release(uploader=uploader)
        self.assertEqual(len(uploader.uploads), 1)
        self.assertIs(second["upload"]["reused_existing_upload"], True)
        self.assertEqual(second["upload"]["video_id"], "vid_new")

    def test_existing_upload_of_this_video_is_reused(self):
        uploader = FakeUploader(existing="vid_old")
        report = self.release(uploader=uploader)
        self.assertEqual(uploader.uploads, [])
        self.assertEqual(report["upload"]["video_id"], "vid_old")

    def test_decisions_may_be_given_as_a_file(self):
        path = Path(self._temp.name) / "decisions.json"
        path.write_text(json.dumps(self.decisions))
        self.assertEqual(self.release(decisions=path)["outcome"], "uploaded_private_verified")


class RefusalTests(_Release):
    def test_requires_explicit_confirmation(self):
        self.assertRefused("confirm_private_upload", confirm=False)
        self.assertRefused("confirm_private_upload", confirm="true")   # only the boolean counts

    def test_public_unlisted_or_scheduled_requests_are_refused(self):
        for privacy in ("public", "unlisted", "PUBLIC"):
            with self.subTest(privacy=privacy):
                self.assertRefused("uploads PRIVATE only", requested_privacy=privacy)
        self.assertRefused("never schedules", publish_at="2026-12-01 18:00")

    def test_missing_or_incomplete_decisions_are_refused(self):
        self.assertRefused("No review decisions were supplied", decisions={})
        self.assertRefused("Cannot read the review decisions", decisions=Path(self._temp.name) / "missing.json")
        partial = approve(self.root)
        partial["scene_reviews"][0]["decision"] = "pending"
        self.assertRefused("the shot decision is 'pending'", decisions=partial)

    def test_stale_changed_or_mismatched_artifacts_are_refused(self):
        self.assertRefused("does not match the hash given for this release", sha="a" * 64)
        self.assertRefused("64-character SHA-256", sha="abc")
        self.assertRefused("Wrong production mode", expected_mode="longform")
        self.assertRefused("Wrong artifact", source_run_id="77")
        self.assertRefused("numeric id", source_run_id="latest")
        self.assertRefused("Wrong job", expected_job_id="some-older-job")
        self.assertRefused("Stale approval", decisions=approve(self.root, video_sha256="b" * 64))
        with open(self.root / "video.mp4", "ab") as handle:
            handle.write(b"changed after review")
        self.assertRefused("Changed video")

    def test_unknown_licence_and_failed_qa_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = make_package(root, assets=[source_asset(rights={"license": "Unknown"}), source_asset()],
                                    qa={"audio": "FAIL"})
            uploader = FakeUploader()
            with fake_media(), self.assertRaises(ReleaseBlocked) as caught:
                release_package(root, approve(root), manifest["video"]["sha256"], True, uploader=uploader)
            self.assertIn("is unclear or not accepted", str(caught.exception))
            self.assertIn("qa/audio.json is FAIL", str(caught.exception))
            self.assertEqual(uploader.uploads, [])

    def test_wrong_or_unverified_channel_is_refused(self):
        self.assertRefused("channel identity could not be verified", uploader=FakeUploader(verified="raise"))
        self.assertRefused("needs a verified channel", uploader=FakeUploader(verified=False))

    def test_uncertain_duplicate_check_is_refused(self):
        self.assertRefused("refusing to risk a duplicate", uploader=FakeUploader(search_error="quotaExceeded"))

    def test_report_is_written_even_when_the_request_is_malformed(self):
        report = self.assertRefused("confirm_private_upload", confirm=False)
        self.assertIsNone(report["upload"])
        self.assertEqual(report["request"]["video_sha256"], self.sha)


class VerificationTests(_Release):
    def expect_unverified(self, uploader, fragment):
        with self.assertRaises(ReleaseUnverified) as caught:
            self.release(uploader=uploader)
        self.assertIn(fragment, str(caught.exception))
        report = self.report()
        self.assertEqual(report["outcome"], "uploaded_unverified")
        self.assertEqual(report["upload"]["video_id"], "vid_new")   # the id is kept for manual follow-up
        return report

    def test_privacy_is_read_back_not_assumed(self):
        report = self.expect_unverified(FakeUploader(privacy="public"), "stored privacy 'public'")
        self.assertEqual(report["upload"]["privacy_status_read_back"], "public")
        result = json.loads((self.reports / "youtube-upload-result.json").read_text())
        self.assertEqual(result["privacy_status"], "public")
        self.assertIs(result["privacy_verified"], False)

    def test_scheduled_or_wrong_channel_or_missing_video_fails_verification(self):
        self.expect_unverified(FakeUploader(publish_at="2026-12-01T00:00:00Z"), "scheduled publish time")
        self.expect_unverified(FakeUploader(video_channel_id="UC_OTHER"), "not the verified channel")
        self.expect_unverified(FakeUploader(found=False), "did not return the uploaded video")
        self.expect_unverified(FakeUploader(processing="failed"), "processing status: failed")

    def test_upload_failure_is_reported_not_hidden(self):
        uploader = FakeUploader()
        with patch.object(uploader, "upload", side_effect=RuntimeError("quotaExceeded")):
            with self.assertRaisesRegex(RuntimeError, "quotaExceeded"):
                self.release(uploader=uploader)
        self.assertEqual(self.report()["outcome"], "upload_failed")


class RecordDecisionTests(_Release):
    def write(self, decisions):
        path = Path(self._temp.name) / "decisions.json"
        path.write_text(json.dumps(decisions))
        return path

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in sorted(self.root.rglob("*")) if path.is_file()}

    def test_approved_decisions_are_recorded_without_touching_evidence(self):
        before = self.snapshot()
        with fake_media():
            result = apply_decisions(self.root, self.write(self.decisions))
        self.assertEqual(result["status"], "APPROVED_FOR_UPLOAD")
        self.assertEqual(result["scene_count"], 2)
        after = self.snapshot()
        for name in before:
            if name.startswith(("qa/", "assets/")) or name in {"video.mp4", "captions.ass"}:
                self.assertEqual(before[name], after[name], name)
        manifest = contract.read_manifest(self.root)
        self.assertEqual(manifest["fingerprint"], self.manifest["fingerprint"])
        self.assertEqual(manifest["scenes"], self.manifest["scenes"])     # rights evidence is not rewritten
        self.assertEqual(manifest["review"]["reviewer"], "Reviewer A")
        with fake_media():
            self.assertTrue(contract.evaluate_release(self.root)["allowed"])   # stored decisions still valid

    def test_rejected_decisions_change_nothing_on_disk(self):
        decisions = approve(self.root)
        decisions["asset_reviews"][0]["rights_decision"] = "rejected"
        before = self.snapshot()
        with fake_media(), self.assertRaises(DecisionsRejected) as caught:
            apply_decisions(self.root, self.write(decisions))
        self.assertIn("rights were rejected", str(caught.exception))
        self.assertEqual(before, self.snapshot())

    def test_approval_recorded_for_one_video_does_not_survive_a_new_render(self):
        with fake_media():
            apply_decisions(self.root, self.write(self.decisions))
            (self.root / "video.mp4").write_bytes(b"\1" * 4096)
            report = contract.evaluate_release(self.root)
            summary = contract.package_status(self.root)
        self.assertFalse(report["allowed"])
        self.assertEqual(summary["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
