"""Upload safety: private-only uploads, channel lock, release gate, duplicate protection."""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import config, dedupe, publish, release_gate, uploader


def _youtube(handle="@engineeringphysics-n5g", video_id="vid123"):
    request = MagicMock()
    request.next_chunk.side_effect = [(None, {"id": video_id})]
    youtube = MagicMock()
    youtube.videos.return_value.insert.return_value = request
    youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "UC_test", "snippet": {"title": "Engineering Physics", "customUrl": handle}}]}
    youtube.search.return_value.list.return_value.execute.return_value = {"items": []}
    return youtube


class UploaderPrivacyTests(unittest.TestCase):
    def setUp(self):
        uploader._VERIFIED_CHANNEL.clear()

    def _upload(self, youtube, **kwargs):
        with patch("core.uploader.MediaFileUpload"), patch("core.uploader._creds"), \
                patch("core.uploader.build", return_value=youtube):
            return uploader.upload(Path("x.mp4"), "Title", "Description", ["tag"], **kwargs)

    def test_public_request_is_downgraded_to_private_without_authorization(self):
        youtube = _youtube()
        with patch.dict(os.environ, {"ALLOW_PUBLIC_PUBLISH": ""}), \
                patch("core.uploader.PUBLISH_AT_IST", "2031-01-01 19:00"), \
                patch("core.uploader._publish_time", return_value="2031-01-01T13:30:00.000Z"):
            self._upload(youtube, privacy_status="public")
        status = youtube.videos.return_value.insert.call_args.kwargs["body"]["status"]
        self.assertEqual(status["privacyStatus"], "private")
        self.assertNotIn("publishAt", status)

    def test_explicit_authorization_is_required_for_non_private(self):
        youtube = _youtube()
        self._upload(youtube, privacy_status="unlisted", authorized=True)
        status = youtube.videos.return_value.insert.call_args.kwargs["body"]["status"]
        self.assertEqual(status["privacyStatus"], "unlisted")
        self.assertEqual(uploader.resolve_privacy("public"), "private")
        self.assertEqual(uploader.resolve_privacy("garbage", authorized=True), "private")

    def test_wrong_channel_is_refused_before_any_upload(self):
        youtube = _youtube(handle="@someone-else")
        with self.assertRaises(uploader.ChannelMismatchError):
            self._upload(youtube)
        youtube.videos.return_value.insert.assert_not_called()

    def test_marker_makes_upload_idempotent(self):
        youtube = _youtube()
        with patch("core.uploader.find_private_video_by_marker", return_value="existing9"):
            self.assertEqual(self._upload(youtube, marker="AI_STUDIO_DEDUPE:shorts:abc"), "existing9")
        youtube.videos.return_value.insert.assert_not_called()
        with patch("core.uploader.find_private_video_by_marker", return_value=None):
            self._upload(youtube, marker="AI_STUDIO_DEDUPE:shorts:abc")
        body = youtube.videos.return_value.insert.call_args.kwargs["body"]
        self.assertIn("AI_STUDIO_DEDUPE:shorts:abc", body["snippet"]["description"])

    def test_scheduling_and_visibility_changes_need_authorization(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
        with patch.dict(os.environ, {"ALLOW_PUBLIC_PUBLISH": ""}):
            with self.assertRaises(uploader.PublicPublishNotAuthorized):
                uploader.schedule_video("vid", future)
            with self.assertRaises(uploader.PublicPublishNotAuthorized):
                uploader.set_video_privacy("vid", "public")
        youtube = MagicMock()
        with patch("core.uploader._creds"), patch("core.uploader.build", return_value=youtube):
            uploader.schedule_video("vid", future, authorized=True)
            uploader.set_video_privacy("vid", "private")
        self.assertEqual(youtube.videos.return_value.update.call_count, 2)


class _Sandbox(unittest.TestCase):
    """Temp build/state dirs plus a throwaway job database."""

    def setUp(self):
        from core import db
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.build, self.state = root / "build", root / "state"
        self.build.mkdir(); self.state.mkdir()
        patches = [patch.object(config, "BUILD_DIR", self.build), patch.object(config, "STATE_DIR", self.state),
                   patch.object(config, "LOG_DIR", root / "logs"), patch.object(db, "DB_PATH", root / "db.sqlite"),
                   patch.dict(os.environ, {"ALLOW_FALLBACK_VISUAL_UPLOAD": "", "ALLOW_DUPLICATE_UPLOAD": "",
                                           "ALLOW_PUBLIC_PUBLISH": "", "TELEGRAM_BOT_TOKEN": "",
                                           "DISCORD_WEBHOOK_URL": "", "SLACK_WEBHOOK_URL": "",
                                           "NOTIFY_WEBHOOK_URL": "", "GITHUB_STEP_SUMMARY": ""})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)
        db.init_db()
        self.db = db

    def make_job(self, title="How a transformer changes voltage", fmt="shorts", quality="PASS",
                 upload_blocked=False, status="done"):
        scenes = [{"say": f"Scene {word} explains how the two coils share one changing magnetic field clearly",
                   "caption": "c", "label": "Coil", "image": "coil", "keywords": ["coil"]}
                  for word in ("one", "two", "three", "four")]
        script = {"title": title, "category": "physics", "scenes": scenes}
        job_id = self.db.save_video(title, fmt, "english", "physics", script,
                                    topic_key=dedupe.topic_key(title))
        video = Path(self.tmp.name) / f"shorts_{job_id}.mp4"
        video.write_bytes(b"0" * 60_000)
        job_dir = self.build / f"job_{job_id}"
        job_dir.mkdir()
        report = {"video_path": str(video), "status": quality, "score": 90, "issues": [],
                  "upload_blocked": upload_blocked, "upload_block_reason": "3 of 4 visuals are fallback cards"}
        (job_dir / "quality_report.json").write_text(json.dumps(report), encoding="utf-8")
        (job_dir / "asset_manifest.json").write_text(json.dumps({"without_provenance": []}), encoding="utf-8")
        self.db.update_video(job_id, video_path=str(video), status=status, quality_status=quality,
                             quality_report_path=str(job_dir / "quality_report.json"))
        return job_id


class ReleaseGateTests(_Sandbox):
    def test_passing_job_is_uploadable(self):
        row = self.db.get_video(self.make_job())
        report = release_gate.assert_uploadable(row)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue((self.build / f"job_{row['id']}" / "release_gate.json").is_file())

    def test_failed_quality_blocks_upload(self):
        row = self.db.get_video(self.make_job(quality="FAIL"))
        with self.assertRaises(release_gate.ReleaseBlocked) as caught:
            release_gate.assert_uploadable(row)
        self.assertIn("technical_qa", str(caught.exception))

    def test_missing_video_and_unfinished_job_block_upload(self):
        job_id = self.make_job(status="building")
        Path(self.db.get_video(job_id)["video_path"]).unlink()
        report = release_gate.evaluate(self.db.get_video(job_id))
        failed = {c["check"] for c in report["checks"] if c["status"] == "FAIL"}
        self.assertTrue({"video_file", "job_status"} <= failed)

    def test_fallback_visual_block_needs_explicit_override(self):
        row = self.db.get_video(self.make_job(quality="REVIEW", upload_blocked=True))
        self.assertEqual(release_gate.evaluate(row)["status"], "BLOCKED")
        with patch.dict(os.environ, {"ALLOW_FALLBACK_VISUAL_UPLOAD": "1"}):
            self.assertEqual(release_gate.evaluate(row)["status"], "PASS")

    def test_unsafe_script_blocks_upload(self):
        job_id = self.make_job()
        script = json.loads(self.db.get_video(job_id)["script_json"])
        script["scenes"][0]["say"] = "This scheme gives guaranteed returns and will double your money in a year"
        self.db.update_video(job_id, script_json=json.dumps(script))
        self.assertEqual(release_gate.evaluate(self.db.get_video(job_id))["status"], "BLOCKED")

    def test_documentary_requires_certification_and_news_requires_verification(self):
        long_row = self.db.get_video(self.make_job(title="The long story of steel", fmt="long"))
        self.assertEqual(long_row["format"], "longform")
        self.assertIn("documentary_certification",
                      " ".join(release_gate.evaluate(long_row)["blocking"]))
        news_row = self.db.get_video(self.make_job(title="Rocket landing test succeeds", fmt="news_short"))
        self.assertIn("news_source_verification", " ".join(release_gate.evaluate(news_row)["blocking"]))


class PublishJobTests(_Sandbox):
    def _uploader(self, stored_privacy="private"):
        mocks = {
            "upload": patch("core.uploader.upload", return_value="yt_new"),
            "status": patch("core.uploader.get_video_status",
                            return_value={"found": True, "privacy_status": stored_privacy, "publish_at": None}),
            "privacy": patch("core.uploader.set_video_privacy"),
            "playlist": patch("core.uploader.add_to_playlist"),
            "thumb": patch("core.uploader.set_video_thumbnail"),
        }
        started = {name: item.start() for name, item in mocks.items()}
        for item in mocks.values():
            self.addCleanup(item.stop)
        return started

    def test_upload_is_private_recorded_and_idempotent(self):
        mocks = self._uploader()
        job_id = self.make_job()
        result = publish.publish_job(job_id, notify=False)
        kwargs = mocks["upload"].call_args.kwargs
        self.assertEqual(kwargs["privacy_status"], "private")
        self.assertTrue(kwargs["disable_scheduling"])
        self.assertTrue(kwargs["marker"].startswith("AI_STUDIO_DEDUPE:shorts:"))
        self.assertEqual(result["privacy"], "private")
        row = self.db.get_video(job_id)
        self.assertEqual((row["youtube_id"], row["status"], row["privacy_status"]), ("yt_new", "uploaded", "private"))
        entry = dedupe.Ledger().find("shorts", row["title"])
        self.assertEqual(entry["youtube_id"], "yt_new")
        again = publish.publish_job(job_id, notify=False)
        self.assertEqual(again["status"], "already_uploaded")
        self.assertEqual(mocks["upload"].call_count, 1)

    def test_same_subject_is_never_uploaded_twice(self):
        mocks = self._uploader()
        publish.publish_job(self.make_job("Compound Interest Kya Hai Aur Yeh Kaise Kaam Karta Hai"), notify=False)
        second = self.make_job("Compound Interest Kya Hai? Samjho Asan Bhasha Mein")
        with self.assertRaises(publish.DuplicateUpload):
            publish.publish_job(second, notify=False)
        self.assertEqual(mocks["upload"].call_count, 1)
        self.assertIsNone(self.db.get_video(second)["youtube_id"])

    def test_upload_recorded_only_in_the_database_still_blocks_a_remake(self):
        mocks = self._uploader()
        first = self.make_job("SIP kya hai? Har mahine thoda thoda nivesh")
        self.db.update_video(first, youtube_id="legacy1", status="uploaded")   # pre-ledger upload
        second = self.make_job("SIP kya hai aur har mahine nivesh kaise karein")
        with self.assertRaises(publish.DuplicateUpload):
            publish.publish_job(second, notify=False)
        mocks["upload"].assert_not_called()
        with patch.dict(os.environ, {"ALLOW_DUPLICATE_UPLOAD": "1"}):
            self.assertEqual(publish.publish_job(second, notify=False)["youtube_id"], "yt_new")

    def test_blocked_job_never_reaches_youtube(self):
        mocks = self._uploader()
        with self.assertRaises(release_gate.ReleaseBlocked):
            publish.publish_job(self.make_job(quality="FAIL"), notify=False)
        mocks["upload"].assert_not_called()

    def test_non_private_result_is_reverted(self):
        mocks = self._uploader(stored_privacy="public")
        result = publish.publish_job(self.make_job(), notify=False)
        mocks["privacy"].assert_called_once_with("yt_new", "private")
        self.assertEqual(result["privacy"], "private")

    def test_dry_run_uploads_nothing(self):
        mocks = self._uploader()
        result = publish.publish_job(self.make_job(), dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("#Shorts", result["metadata"]["title"])
        mocks["upload"].assert_not_called()

    def test_upload_failure_is_recorded_and_job_stays_retryable(self):
        self._uploader()
        job_id = self.make_job()
        with patch("core.uploader.upload", side_effect=RuntimeError("quotaExceeded")):
            with self.assertRaises(RuntimeError):
                publish.publish_job(job_id, notify=False)
        row = self.db.get_video(job_id)
        self.assertIsNone(row["youtube_id"])
        self.assertEqual(row["status"], "done")

    def test_latest_unuploaded_respects_format(self):
        short_id = self.make_job("Short topic alpha")
        self.make_job("Documentary topic beta", fmt="longform")
        self.assertEqual(publish.latest_unuploaded("shorts")["id"], short_id)
        self.assertEqual(publish.latest_unuploaded("long")["format"], "longform")


if __name__ == "__main__":
    unittest.main()
