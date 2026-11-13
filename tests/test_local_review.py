"""Local uploads need the same human review as the GitHub release workflows."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, dedupe, local_review, publish, release_gate
from core import review_contract as contract
from tests.review_package_fixture import approve, fake_media, have_ffmpeg, make_package, render_real_video

NASA = {"provider": "NASA", "page_url": "https://images.nasa.gov/details/PIA00001", "title": "Mars",
        "creator": "NASA/JPL", "licence": "Public Domain", "licence_url": "https://www.nasa.gov/media-guidelines",
        "attribution_text": "NASA/JPL"}


class _Sandbox(unittest.TestCase):
    def setUp(self):
        from core import db
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.build, self.output = self.root / "build", self.root / "output"
        self.build.mkdir(); self.output.mkdir()
        for item in (patch.object(config, "BUILD_DIR", self.build), patch.object(config, "OUTPUT_DIR", self.output),
                     patch.object(config, "STATE_DIR", self.root / "state"),
                     patch.object(config, "LOG_DIR", self.root / "logs"),
                     patch.object(db, "DB_PATH", self.root / "db.sqlite"),
                     patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "DISCORD_WEBHOOK_URL": "",
                                             "SLACK_WEBHOOK_URL": "", "NOTIFY_WEBHOOK_URL": "",
                                             "GITHUB_STEP_SUMMARY": "", "GITHUB_RUN_ID": "",
                                             "ALLOW_DUPLICATE_UPLOAD": "", "ALLOW_PUBLIC_PUBLISH": ""})):
            item.start()
            self.addCleanup(item.stop)
        db.init_db()
        self.db = db

    def make_job(self, fmt="shorts", title="Why Mars is red", real_video=False):
        """A finished job with the build outputs the Shorts pipeline writes."""
        scenes = [{"say": f"Scene {word} explains how iron oxide dust colours the whole surface of the planet",
                   "caption": "c", "label": "Mars", "image": "Mars from orbit", "keywords": ["mars"]}
                  for word in ("one", "two", "three", "four")]
        script = {"title": title, "category": "science", "language": "english", "scenes": scenes}
        job_id = self.db.save_video(title, fmt, "english", "science", script, topic_key=dedupe.topic_key(title))
        job_dir = self.build / f"job_{job_id}"
        job_dir.mkdir()
        video = self.output / f"shorts_{job_id}.mp4"
        if real_video:
            render_real_video(video, "shortform")
        else:
            video.write_bytes(b"\0" * 60_000)
        assets = []
        for index in range(len(scenes)):
            name = f"img{index}.jpg"
            (job_dir / name).write_bytes(b"jpeg-%d" % index)
            (job_dir / f"{name}.source.json").write_text(json.dumps(NASA))
            assets.append({"scene": index + 1, "file": name})
        (job_dir / "asset_manifest.json").write_text(json.dumps({"assets": assets, "without_provenance": []}))
        (job_dir / "script.json").write_text(json.dumps(script))
        (job_dir / "subs.ass").write_text("[Script Info]\n[Events]\n")
        (job_dir / "quality_report.json").write_text(json.dumps(
            {"video_path": str(video), "status": "PASS", "score": 95, "issues": []}))
        (job_dir / "narration_synthesis_report.json").write_text(json.dumps(
            {"requested_engine": "edge", "actual_engine": "edge", "voice": "en-GB-RyanNeural", "language": "english"}))
        if fmt == "news_short":
            (job_dir / "verification.json").write_text(json.dumps({"status": "PASS", "sources": [
                {"source_id": "N1", "title": "T", "publisher": "Reuters", "url": "https://reuters.com/a"}]}))
        self.db.update_video(job_id, video_path=str(video), status="done", quality_status="PASS",
                             quality_report_path=str(job_dir / "quality_report.json"))
        return job_id

    def row(self, job_id):
        return self.db.get_video(job_id)

    def build_package(self, job_id):
        with fake_media("shortform"), contextlib.redirect_stdout(io.StringIO()):
            return local_review.build_package(self.row(job_id))

    def save_decisions(self, job_id, **changes):
        package = local_review.package_dir(job_id)
        decisions = approve(package, **changes)
        (package / contract.DECISIONS_NAME).write_text(json.dumps(decisions))
        return decisions

    def status(self, job_id):
        with fake_media("shortform"):
            return local_review.review_status(self.row(job_id))

    def gate(self, job_id):
        with fake_media("shortform"):
            return release_gate.evaluate(self.row(job_id))


class ReviewStatusTests(_Sandbox):
    def test_fresh_build_has_no_review_and_the_message_says_what_to_do(self):
        job_id = self.make_job()
        state = self.status(job_id)
        self.assertFalse(state["ok"])
        self.assertIn("no review package exists", state["detail"])
        self.assertIn(f"python run.py review --id {job_id}", state["detail"])

    def test_package_without_decisions_or_with_the_untouched_template_is_not_approved(self):
        job_id = self.make_job()
        summary = self.build_package(job_id)
        self.assertEqual(summary["status"], "REVIEW_REQUIRED", summary.get("errors"))
        self.assertTrue(Path(summary["dashboard"]).is_file())
        self.assertIn("no human decisions yet", self.status(job_id)["detail"])
        package = Path(summary["package_dir"])
        shutil.copy(package / contract.TEMPLATE_NAME, package / contract.DECISIONS_NAME)
        state = self.status(job_id)
        self.assertFalse(state["ok"])
        self.assertIn("human review is not complete", state["detail"])

    def test_complete_decisions_for_the_exact_file_are_accepted(self):
        job_id = self.make_job()
        self.build_package(job_id)
        self.save_decisions(job_id)
        state = self.status(job_id)
        self.assertTrue(state["ok"], state["detail"])
        self.assertEqual(state["video_sha256"], contract.sha256_file(self.row(job_id)["video_path"]))
        gate = self.gate(job_id)
        self.assertEqual(gate["status"], "PASS", gate["blocking"])
        self.assertTrue(gate["human_review"]["approved"])

    def test_one_pending_item_blocks(self):
        job_id = self.make_job()
        self.build_package(job_id)
        decisions = self.save_decisions(job_id)
        decisions["asset_reviews"][0]["rights_decision"] = "pending"
        (local_review.package_dir(job_id) / contract.DECISIONS_NAME).write_text(json.dumps(decisions))
        state = self.status(job_id)
        self.assertFalse(state["ok"])
        self.assertEqual(self.gate(job_id)["status"], "BLOCKED")

    def test_re_rendered_video_invalidates_the_review(self):
        job_id = self.make_job()
        self.build_package(job_id)
        self.save_decisions(job_id)
        Path(self.row(job_id)["video_path"]).write_bytes(b"\1" * 60_000)      # a new render of the same job
        state = self.status(job_id)
        self.assertFalse(state["ok"])
        self.assertIn("artifact identity", " ".join(state["blocking"]))

    def test_rebuilt_package_does_not_inherit_old_decisions(self):
        job_id = self.make_job()
        self.build_package(job_id)
        old = self.save_decisions(job_id)
        with patch.object(contract, "make_job_id", return_value="shortform-local20990101T000000Z-j1"):
            self.build_package(job_id)                                         # rebuild: decisions file is gone
        package = local_review.package_dir(job_id)
        self.assertFalse((package / contract.DECISIONS_NAME).exists())
        (package / contract.DECISIONS_NAME).write_text(json.dumps(old))        # replaying the old approval
        self.assertFalse(self.status(job_id)["ok"])

    def test_decisions_of_another_job_are_refused(self):
        first, second = self.make_job(), self.make_job(title="Why Venus is hot")
        self.build_package(first)
        self.build_package(second)
        decisions = self.save_decisions(first)
        (local_review.package_dir(second) / contract.DECISIONS_NAME).write_text(json.dumps(decisions))
        self.assertFalse(self.status(second)["ok"])

    def test_record_decisions_writes_nothing_when_refused(self):
        from scripts.apply_review_decisions import DecisionsRejected
        job_id = self.make_job()
        with self.assertRaises(local_review.ReviewUnavailable):
            local_review.record_decisions(self.row(job_id), self.root / "none.json")
        self.build_package(job_id)
        package = local_review.package_dir(job_id)
        with fake_media("shortform"), self.assertRaises(DecisionsRejected):
            local_review.record_decisions(self.row(job_id), package / contract.TEMPLATE_NAME)
        self.assertFalse((package / contract.DECISIONS_NAME).exists())
        exported = self.root / "exported.json"
        exported.write_text(json.dumps(approve(package)))
        with fake_media("shortform"):
            applied = local_review.record_decisions(self.row(job_id), exported)
        self.assertEqual(applied["reviewer"], "Reviewer A")
        self.assertTrue(self.status(job_id)["ok"])

    def test_modes_follow_the_job_format(self):
        self.assertEqual(local_review.mode_for({"format": "shorts"}), "shortform")
        self.assertEqual(local_review.mode_for({"format": "news_short"}), "daily_news")
        self.assertEqual(local_review.mode_for({"format": "long"}), "longform")
        news = self.make_job(fmt="news_short", title="Rocket landing test succeeds")
        with fake_media("daily_news"), contextlib.redirect_stdout(io.StringIO()):
            summary = local_review.build_package(self.row(news))
        manifest = json.loads((Path(summary["package_dir"]) / contract.MANIFEST_NAME).read_text())
        self.assertEqual(manifest["mode"], "daily_news")

    def test_unbuildable_jobs_are_reported_not_raised_as_tracebacks(self):
        job_id = self.make_job()
        Path(self.row(job_id)["video_path"]).unlink()
        with self.assertRaisesRegex(local_review.ReviewUnavailable, "no rendered video"):
            local_review.build_package(self.row(job_id))
        other = self.make_job(title="Another video")
        (self.build / f"job_{other}" / "subs.ass").unlink()
        with fake_media("shortform"), self.assertRaisesRegex(local_review.ReviewUnavailable, "captions"):
            local_review.build_package(self.row(other))


class LocalUploadTests(_Sandbox):
    def _uploader(self):
        mocks = {"upload": patch("core.uploader.upload", return_value="yt_new"),
                 "status": patch("core.uploader.get_video_status",
                                 return_value={"found": True, "privacy_status": "private", "publish_at": None}),
                 "privacy": patch("core.uploader.set_video_privacy"),
                 "playlist": patch("core.uploader.add_to_playlist"),
                 "thumb": patch("core.uploader.set_video_thumbnail")}
        started = {name: item.start() for name, item in mocks.items()}
        for item in mocks.values():
            self.addCleanup(item.stop)
        return started

    def test_upload_is_refused_until_the_review_is_complete_then_uploads_private(self):
        mocks = self._uploader()
        job_id = self.make_job()
        with fake_media("shortform"), self.assertRaises(release_gate.ReleaseBlocked) as caught:
            publish.publish_job(job_id, notify=False)
        self.assertIn("human_review", str(caught.exception))
        self.build_package(job_id)
        with fake_media("shortform"), self.assertRaises(release_gate.ReleaseBlocked):
            publish.publish_job(job_id, notify=False)
        mocks["upload"].assert_not_called()

        self.save_decisions(job_id)
        with fake_media("shortform"), contextlib.redirect_stdout(io.StringIO()):
            result = publish.publish_job(job_id, notify=False)
        self.assertEqual(result["privacy"], "private")
        args, kwargs = mocks["upload"].call_args
        self.assertEqual(kwargs["privacy_status"], "private")
        self.assertTrue(kwargs["disable_scheduling"])
        sha = contract.sha256_file(self.row(job_id)["video_path"])
        self.assertIn(f"AI-STUDIO-REVIEWED-SHA256:{sha}", args[2])
        self.assertEqual(Path(args[0]), Path(self.row(job_id)["video_path"]))

    def test_run_py_review_and_upload_commands(self):
        import run
        mocks = self._uploader()
        job_id = self.make_job()

        def cli(*argv):
            buffer = io.StringIO()
            with patch.object(sys, "argv", ["run.py", *argv]), fake_media("shortform"), \
                    contextlib.redirect_stdout(buffer), patch.object(run, "send"):
                try:
                    run.main()
                    return 0, buffer.getvalue()
                except SystemExit as exc:
                    return exc.code, buffer.getvalue()

        code, _ = cli("upload", "--id", str(job_id))
        self.assertIn("human_review", str(code))                       # refused, with the reason
        code, output = cli("review", "--id", str(job_id))
        self.assertEqual(code, 0)
        self.assertIn("review_dashboard.html", output)
        self.assertIn(f"python run.py upload --id {job_id} --decisions", output)
        package = local_review.package_dir(job_id)
        code, _ = cli("upload", "--id", str(job_id), "--decisions", str(package / contract.TEMPLATE_NAME))
        self.assertIn("Review decisions refused; nothing was uploaded", str(code))
        mocks["upload"].assert_not_called()
        exported = self.root / "exported.json"
        exported.write_text(json.dumps(approve(package)))
        code, output = cli("upload", "--id", str(job_id), "--decisions", str(exported))
        self.assertEqual(code, 0, output)
        mocks["upload"].assert_called_once()
        self.assertEqual(self.row(job_id)["youtube_id"], "yt_new")

    def test_build_with_upload_flag_prepares_the_review_instead_of_uploading(self):
        import run
        mocks = self._uploader()
        job_id = self.make_job()
        with fake_media("shortform"), contextlib.redirect_stdout(io.StringIO()) as buffer:
            run._upload(job_id)
        mocks["upload"].assert_not_called()
        self.assertIn("cannot be uploaded before a person has reviewed it", buffer.getvalue())
        self.assertTrue((local_review.package_dir(job_id) / "review_dashboard.html").is_file())


@unittest.skipUnless(have_ffmpeg(), "FFmpeg/FFprobe are required")
class RealMediaLocalReviewTests(_Sandbox):
    def test_real_video_is_probed_decoded_and_matched_by_hash(self):
        job_id = self.make_job(real_video=True)
        with contextlib.redirect_stdout(io.StringIO()):
            summary = local_review.build_package(self.row(job_id))
        self.assertEqual(summary["status"], "REVIEW_REQUIRED", summary.get("errors"))
        package = Path(summary["package_dir"])
        (package / contract.DECISIONS_NAME).write_text(json.dumps(approve(package)))
        self.assertTrue(local_review.review_status(self.row(job_id))["ok"])
        render_real_video(Path(self.row(job_id)["video_path"]), "shortform", seconds=4)
        self.assertFalse(local_review.review_status(self.row(job_id))["ok"])


if __name__ == "__main__":
    unittest.main()
