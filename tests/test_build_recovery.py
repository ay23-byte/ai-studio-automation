"""Bounded in-run recovery of a failed build (core/recovery.py, scripts/build_with_recovery.py)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, db, dedupe, recovery

SCRIPT = {"title": "Why ice floats", "scenes": [{"say": "Ice is less dense than water."}]}
QUIET = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "DISCORD_WEBHOOK_URL": "", "SLACK_WEBHOOK_URL": "",
         "NOTIFY_WEBHOOK_URL": "", "GITHUB_STEP_SUMMARY": "", "GITHUB_ACTIONS": "",
         "BUILD_RECOVERY_DELAY_SECONDS": "0", "BUILD_RECOVERY_ATTEMPTS": "1"}


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for item in (patch.object(config, "BUILD_DIR", self.root / "build"),
                     patch.object(config, "OUTPUT_DIR", self.root / "output"),
                     patch.object(config, "LOG_DIR", self.root / "logs"),
                     patch.object(config, "STATE_DIR", self.root / "state"),
                     patch.object(db, "DB_PATH", self.root / "studio.sqlite"),
                     patch("core.dedupe.known_titles_from_db", return_value=[]),
                     patch.dict(os.environ, QUIET)):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)
        db.init_db()

    def failed_job(self, error, fmt="shorts", **extra):
        job_id = db.save_video(SCRIPT["title"], fmt, "english", "science", SCRIPT,
                               topic_key=dedupe.topic_key("Why ice floats"), **extra)
        db.update_video(job_id, status="failed", error=error)
        return job_id


class ClassificationTests(unittest.TestCase):
    def test_transient_failures_are_retryable(self):
        for error in ("FFmpegError: Command returned non-zero exit status 1",
                      "RuntimeError: No scene-matched visual was available for Shorts scene 2. Retry later "
                      "or configure a working image provider/Pixabay key.",
                      "ConnectionError: HTTPSConnectionPool(host='image.pollinations.ai') timed out",
                      "LLMError: every provider was unavailable",
                      ""):
            with self.subTest(error=error):
                self.assertTrue(recovery.classify_error(error)[0])

    def test_failures_that_would_repeat_are_never_retried(self):
        cases = {
            "GeminiCredentialError: API key not valid": "credentials",
            "GeminiConfigError: 'models/x y' is not a valid model id": "configuration",
            "ValueError: Shorts script failed content QA: too few scenes": "invalid script or configuration",
            "RuntimeError: Video quality inspection failed; refusing to mark this render complete.": "quality gate",
            "FFmpegUnavailable: FFmpeg is not installed": "missing tool",
            "VoiceProviderUnavailable: no narration provider": "no narration provider installed",
            "ReleaseBlocked: Upload blocked: technical_qa": "release gate",
            "DuplicateUpload: already uploaded": "duplicate",
            "RuntimeError: 401 Unauthorized from provider": "credentials",
            "KeyboardInterrupt: ": "cancelled",
        }
        for error, reason in cases.items():
            with self.subTest(error=error):
                self.assertEqual(recovery.classify_error(error), (False, reason))

    def test_attempts_are_bounded(self):
        for raw, expected in (("0", 0), ("1", 1), ("9", 3), ("-4", 0), ("abc", 1), ("", 1)):
            with patch.dict(os.environ, {"BUILD_RECOVERY_ATTEMPTS": raw}):
                self.assertEqual(recovery.max_attempts(), expected)


class ResumeTests(_Sandbox):
    def test_only_jobs_created_by_this_run_are_considered(self):
        old = self.failed_job("RuntimeError: old failure")
        before = recovery.job_ids()
        self.assertIsNone(recovery.find_failed_job(before))
        new = self.failed_job("RuntimeError: new failure")
        self.assertEqual(recovery.find_failed_job(before)["id"], new)
        self.assertNotEqual(old, new)

    def test_longform_and_scriptless_jobs_are_not_resumed_automatically(self):
        long_id = self.failed_job("RuntimeError: x", fmt="longform")
        self.assertFalse(recovery.can_resume(db.get_video(long_id))[0])
        short_id = self.failed_job("RuntimeError: x")
        db.update_video(short_id, script_json="")
        self.assertFalse(recovery.can_resume(db.get_video(short_id))[0])

    def test_resume_rebuilds_and_records_the_job_like_the_normal_path(self):
        topics_file = self.root / "topics.json"
        topics_file.write_text(json.dumps([{"title": "Why ice floats"}, {"title": "Other topic"}]))
        job_id = self.failed_job("FFmpegError: render crashed")
        out = self.root / "output" / f"shorts_{job_id}.mp4"

        def fake_build(script, vid, language="english", **_):
            self.assertEqual(script["title"], SCRIPT["title"])
            db.update_video(vid, status="done", video_path=str(out))
            return out

        with patch("core.pipeline.build_shorts", side_effect=fake_build) as build, \
                patch("core.topics.TOPICS_PATH", topics_file):
            result = recovery.resume(db.get_video(job_id))
        self.assertEqual(result, str(out))
        build.assert_called_once()
        row = db.get_video(job_id)
        self.assertEqual(row["attempts"], 1)
        entries = dedupe.Ledger().entries
        self.assertEqual([(e["kind"], e["status"], e["job_id"]) for e in entries], [("shorts", "built", job_id)])
        self.assertEqual(entries[0]["key"], dedupe.topic_key("Why ice floats"))
        stored = json.loads(topics_file.read_text())
        self.assertTrue(stored[0].get("done"))
        self.assertFalse(stored[1].get("done"))

    def test_recovered_news_job_marks_its_sources_seen(self):
        job_id = self.failed_job("FFmpegError: render crashed", fmt="news_short", source_ref="src-a,src-b")
        with patch("core.pipeline.build_shorts", return_value=self.root / "x.mp4"):
            recovery.resume(db.get_video(job_id))
        seen = json.loads((self.root / "state" / "daily_news" / "seen.json").read_text())
        self.assertEqual(seen["ids"], ["src-a", "src-b"])
        entry = dedupe.Ledger().entries[0]
        self.assertEqual((entry["kind"], entry["source_refs"]), ("news_short", ["src-a", "src-b"]))


class CommandTests(_Sandbox):
    """The wrapper, driven with tiny real subprocesses so exit codes are genuine."""

    def run_wrapper(self, body, *flags):
        from scripts import build_with_recovery
        script = self.root / "cmd.py"
        script.write_text(body)
        return build_with_recovery.main([*flags, "--", sys.executable, str(script)])

    def test_success_and_upload_blocked_pass_straight_through(self):
        with patch("core.recovery.resume") as resume:
            self.assertEqual(self.run_wrapper("print('ok')"), 0)
            self.assertEqual(self.run_wrapper("import sys; sys.exit(3)"), 3)
        resume.assert_not_called()

    def test_failed_job_is_resumed_once_and_the_step_succeeds(self):
        marker = self.root / "created"
        # The child cannot share the patched module state, so the test creates the
        # failed job at the moment the child exits non-zero.
        body = f"import sys, pathlib; pathlib.Path({str(marker)!r}).write_text('1'); sys.exit(1)"
        from scripts import build_with_recovery
        real_run = build_with_recovery._run

        def run_and_fail(command):
            code, tail = real_run(command)
            self.job_id = self.failed_job("FFmpegError: render crashed")
            return code, tail

        with patch.object(build_with_recovery, "_run", side_effect=run_and_fail), \
                patch("core.recovery.resume", return_value=str(self.root / "shorts_1.mp4")) as resume:
            self.assertEqual(self.run_wrapper(body), 0)
        self.assertTrue(marker.exists())
        self.assertEqual(resume.call_args[0][0]["id"], self.job_id)

    def test_non_retryable_job_failure_keeps_the_original_exit_code(self):
        from scripts import build_with_recovery
        real_run = build_with_recovery._run

        def run_and_fail(command):
            code, tail = real_run(command)
            self.failed_job("GeminiCredentialError: API key not valid")
            return code, tail

        with patch.object(build_with_recovery, "_run", side_effect=run_and_fail), \
                patch("core.recovery.resume") as resume:
            self.assertEqual(self.run_wrapper("import sys; sys.exit(7)"), 7)
        resume.assert_not_called()

    def test_failure_before_a_job_exists_reruns_the_command_once(self):
        counter = self.root / "count"
        body = (f"import sys, pathlib; p = pathlib.Path({str(counter)!r}); "
                "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); "
                "sys.exit(0 if n >= 2 else 1)")
        self.assertEqual(self.run_wrapper(body), 0)
        self.assertEqual(counter.read_text(), "2")

    def test_rerun_is_bounded_and_a_credential_error_is_not_rerun(self):
        counter = self.root / "count"
        count = (f"import sys, pathlib; p = pathlib.Path({str(counter)!r}); "
                 "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); ")
        self.assertEqual(self.run_wrapper(count + "sys.exit(5)"), 5)
        self.assertEqual(counter.read_text(), "2")           # original + one retry, never more
        counter.unlink()
        body = count + "raise ValueError('GEMINI_MODEL is not a valid model id')"
        self.assertEqual(self.run_wrapper(body), 1)
        self.assertEqual(counter.read_text(), "1")           # deterministic: not rerun

    def test_recovery_can_be_switched_off(self):
        counter = self.root / "count"
        body = (f"import sys, pathlib; p = pathlib.Path({str(counter)!r}); "
                "n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n)); sys.exit(1)")
        with patch.dict(os.environ, {"BUILD_RECOVERY_ATTEMPTS": "0"}):
            self.assertEqual(self.run_wrapper(body), 1)
        self.assertEqual(counter.read_text(), "1")

    def test_a_finished_job_is_never_rebuilt_when_a_later_step_fails(self):
        from scripts import build_with_recovery
        real_run = build_with_recovery._run
        calls = []

        def run_then_done(command):
            calls.append(1)
            code, tail = real_run(command)
            job_id = db.save_video("Done video", "shorts", "english", "science", SCRIPT)
            db.update_video(job_id, status="done")
            return code, tail

        with patch.object(build_with_recovery, "_run", side_effect=run_then_done), \
                patch("core.recovery.resume") as resume:
            self.assertEqual(self.run_wrapper("import sys; sys.exit(1)"), 1)
        resume.assert_not_called()
        self.assertEqual(len(calls), 1)

    def test_upload_after_recovery_goes_through_the_release_gate(self):
        from core.release_gate import ReleaseBlocked
        from scripts import build_with_recovery
        real_run = build_with_recovery._run

        def run_and_fail(command):
            code, tail = real_run(command)
            self.job_id = self.failed_job("FFmpegError: render crashed", fmt="news_short")
            return code, tail

        with patch.object(build_with_recovery, "_run", side_effect=run_and_fail), \
                patch("core.recovery.resume", return_value="x.mp4"), \
                patch("core.publish.publish_job", side_effect=ReleaseBlocked("blocked: fallback visuals")) as publish:
            code = self.run_wrapper("import sys; sys.exit(1)", "--upload-after-recovery")
        self.assertEqual(code, 3)                          # built, upload blocked
        publish.assert_called_once_with(self.job_id, wait=True)


if __name__ == "__main__":
    unittest.main()
