"""The per-run report and GitHub job summary (core/run_report.py, scripts/write_job_summary.py)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from core import config, db, run_report

QUIET = {"GITHUB_STEP_SUMMARY": "", "GITHUB_ACTIONS": "", "GITHUB_RUN_ID": "", "GITHUB_REPOSITORY": "",
         "GITHUB_SERVER_URL": "", "JOB_STATUS": "", "UPLOAD_REQUESTED": "",
         "RUN_REPORT_SINCE": "", "RUN_REPORT_ALERT_WINDOW_HOURS": ""}
SCRIPT = {"title": "Why ice floats", "scenes": [{"say": "Ice is less dense than water."}],
          "generation": {"provider": "gemini", "model": "gemini-3.7-flash", "fallback_used": True}}
QUALITY = {"status": "PASS", "score": 91, "issues": [], "warnings": ["Slightly quiet narration."],
           "metrics": {"duration_seconds": 31.4, "width": 1080, "height": 1920,
                       "video_codec": "h264", "audio_codec": "aac"}}


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for item in (patch.object(config, "BUILD_DIR", self.root / "build"),
                     patch.object(config, "OUTPUT_DIR", self.root / "output"),
                     patch.object(config, "LOG_DIR", self.root / "logs"),
                     patch.object(config, "STATE_DIR", self.root / "state"),
                     patch.object(db, "DB_PATH", self.root / "studio.sqlite"),
                     patch.dict(os.environ, QUIET)):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)
        (self.root / "output").mkdir()
        db.init_db()

    def job(self, *, fmt="shorts", status="done", video=True, files=None, **columns):
        job_id = db.save_video(SCRIPT["title"], fmt, "english", "science", SCRIPT)
        build = self.root / "build" / f"job_{job_id}"
        build.mkdir(parents=True)
        (build / "script.json").write_text(json.dumps(SCRIPT))
        for name, payload in (files or {}).items():
            (build / name).write_text(payload if isinstance(payload, str) else json.dumps(payload))
        path = self.root / "output" / f"{fmt}_{job_id}.mp4"
        if video:
            path.write_bytes(b"0" * 2_097_152)
        db.update_video(job_id, status=status, video_path=str(path) if video else None, **columns)
        return job_id, build

    def notify(self, message, level="warning", age_hours=0.0):
        log = self.root / "logs"
        log.mkdir(exist_ok=True)
        logged = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        with (log / "notifications.jsonl").open("a") as handle:
            handle.write(json.dumps({"time": logged.isoformat(timespec="seconds"),
                                     "level": level, "message": message}) + "\n")


class OutcomeTests(_Sandbox):
    def test_built_job_reports_every_required_field(self):
        job_id, _ = self.job(files={
            "quality_report.json": QUALITY,
            "narration_synthesis_report.json": {"requested_engine": "edge", "actual_engine": "edge",
                                                "voice": "en-IN-PrabhatNeural", "language": "english"},
            "asset_manifest.json": {"asset_count": 2, "fallback_card_count": 0, "without_provenance": [],
                                    "assets": [{"provider": "pixabay"}, {"provider": "pollinations"}]},
            "licenses.csv": "shot_id,source\nshot_0001,Pixabay\nshot_0002,Pollinations\n",
            "credits.txt": "credits",
        })
        report = run_report.collect("shorts", job_status="success", artifact="shorts-review-9")
        self.assertEqual(report["outcome"], run_report.BUILT)
        self.assertEqual(report["job_id"], job_id)
        self.assertEqual(report["output"]["duration_seconds"], 31.4)
        self.assertEqual((report["output"]["width"], report["output"]["height"]), (1080, 1920))
        self.assertEqual(report["output"]["size_mb"], 2.0)
        self.assertEqual(report["voice"]["provider"], "edge")
        self.assertEqual(report["visuals"]["providers"], {"pixabay": 1, "pollinations": 1})
        self.assertEqual(report["rights"]["licence_rows"], 2)
        self.assertEqual(report["qa"]["status"], "PASS")
        self.assertEqual(report["upload"]["status"], "not attempted")
        self.assertIsNone(report["failure_reason"])
        markdown = run_report.to_markdown(report)
        for label in ("Final status", "Topic", "Output file", "Duration", "Resolution", "Voice provider",
                      "Visual providers", "Research status", "Rights status", "QA status", "Upload status",
                      "Artifact"):
            self.assertIn(f"**{label}**", markdown)
        self.assertIn("BUILT, NOT UPLOADED", markdown)
        self.assertIn("1080x1920", markdown)
        self.assertIn("shorts-review-9", markdown)
        self.assertIn("gemini-3.7-flash (fallback)", markdown)

    def test_upload_is_reported_only_with_a_video_id_and_states_whether_privacy_was_read_back(self):
        _, build = self.job(status="uploaded", files={"quality_report.json": QUALITY},
                            youtube_id="abc123XYZ00", privacy_status="private")
        (build / "upload_result.json").write_text(json.dumps(
            {"youtube_id": "abc123XYZ00", "url": "https://youtu.be/abc123XYZ00", "privacy": "private",
             "privacy_verified": False}))
        report = run_report.collect("shorts", job_status="success")
        self.assertEqual(report["outcome"], run_report.UPLOADED)
        self.assertFalse(report["upload"]["privacy_verified"])
        self.assertIn("privacy NOT read back from YouTube", run_report.to_markdown(report))
        (build / "upload_result.json").write_text(json.dumps(
            {"youtube_id": "abc123XYZ00", "privacy": "private", "privacy_verified": True}))
        self.assertIn("privacy read back from YouTube", run_report.to_markdown(run_report.collect("shorts")))

    def test_release_gate_block_is_not_reported_as_an_upload(self):
        self.job(files={"quality_report.json": QUALITY,
                        "release_gate.json": {"status": "BLOCKED",
                                              "blocking": ["visual_fallback_share: 4 of 4 visuals are fallback cards"]}})
        report = run_report.collect("shorts", job_status="failure", upload_requested=True)
        self.assertEqual(report["outcome"], run_report.UPLOAD_BLOCKED)
        self.assertIn("visual_fallback_share", report["failure_reason"])
        self.assertTrue(run_report.annotation(report).startswith("::warning title=Short built but not uploaded::"))

    def test_failed_build_names_the_stage_and_the_error(self):
        self.job(status="failed", video=False, error="RuntimeError: ffmpeg exited with code 1",
                 files={"checkpoint.json": {"stages": {
                     "voice": {"status": "done", "duration_seconds": 12.0},
                     "render": {"status": "running"}}}})
        report = run_report.collect("shorts", job_status="failure")
        self.assertEqual(report["outcome"], run_report.FAILED)
        self.assertIn("ffmpeg exited with code 1", report["failure_reason"])
        self.assertIn("stage 'render' did not finish", report["failure_reason"])
        self.assertTrue(run_report.annotation(report).startswith("::error title=Short failed::"))
        self.assertIn("no video file was produced", run_report.to_markdown(report))

    def test_a_job_marked_done_without_its_video_file_is_a_failure(self):
        self.job(status="done", video=False, files={"quality_report.json": QUALITY})
        self.assertEqual(run_report.collect("shorts", job_status="success")["outcome"], run_report.FAILED)

    def test_no_job_is_skipped_on_success_and_failed_on_failure(self):
        self.notify("ℹ️ Daily news Short skipped: No story was corroborated.", level="info")
        skipped = run_report.collect("news", job_status="success")
        self.assertEqual(skipped["outcome"], run_report.SKIPPED)
        self.assertIn("No story was corroborated", skipped["skip_reason"])
        self.assertTrue(run_report.annotation(skipped).startswith("::notice title=News Short skipped::"))
        self.notify("❌ Auto-generate failed for 'X': every text model was unavailable", level="error")
        failed = run_report.collect("shorts", job_status="failure")
        self.assertEqual(failed["outcome"], run_report.FAILED)
        self.assertIn("every text model was unavailable", failed["failure_reason"])
        self.assertIn("none (no job was created)", run_report.to_markdown(failed))

    def test_alerts_from_an_earlier_run_are_never_quoted_as_the_reason(self):
        self.notify("⚠️ Upload blocked for job #1: fallback visuals above the limit", age_hours=30)
        self.notify("ℹ️ Daily news Short skipped: yesterday's reason", level="info", age_hours=26)
        skipped = run_report.collect("news", job_status="success")
        self.assertEqual(skipped["outcome"], run_report.SKIPPED)
        self.assertNotIn("Upload blocked", skipped["skip_reason"])
        self.assertNotIn("yesterday", skipped["skip_reason"])
        self.assertIn("No job was created in this run", skipped["skip_reason"])
        failed = run_report.collect("shorts", job_status="failure")
        self.assertNotIn("Upload blocked", failed["failure_reason"])
        self.assertIn("stopped before a job was created", failed["failure_reason"])

    def test_alert_cutoff_can_be_set_exactly_and_untimed_entries_are_ignored(self):
        self.notify("❌ Auto-generate failed: ten minutes ago", level="error", age_hours=10 / 60)
        log = self.root / "logs" / "notifications.jsonl"
        with log.open("a") as handle:
            handle.write(json.dumps({"level": "error", "message": "❌ entry without a time"}) + "\n")
        since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
        with mock.patch.dict(os.environ, {"RUN_REPORT_SINCE": since}):
            self.assertEqual(run_report._recent_alerts(), [])
        with mock.patch.dict(os.environ, {"RUN_REPORT_SINCE": "", "RUN_REPORT_ALERT_WINDOW_HOURS": "1"}):
            self.assertEqual(run_report._recent_alerts(), ["❌ Auto-generate failed: ten minutes ago"])

    def test_the_run_level_alert_is_never_quoted_back_as_its_own_reason(self):
        self.notify("❌ AI Studio make-shorts failed\nNo run report was written; see the failing step.", level="error")
        failed = run_report.collect("shorts", job_status="failure")
        self.assertNotIn("AI Studio make-shorts failed", failed["failure_reason"])
        self.assertIn("stopped before a job was created", failed["failure_reason"])

    def test_rows_without_a_build_directory_are_never_mistaken_for_this_run(self):
        # The tracked database ships with historical rows; a fresh runner has no build/ for them.
        db.save_video("Old video", "shorts", "english", "science", SCRIPT)
        self.assertIsNone(run_report.find_job("shorts"))
        self.assertEqual(run_report.collect("shorts", job_status="success")["outcome"], run_report.SKIPPED)

    def test_pipeline_filter_separates_news_from_evergreen_shorts(self):
        short_id, _ = self.job(fmt="shorts", files={"quality_report.json": QUALITY})
        news_id, build = self.job(fmt="news_short", files={"quality_report.json": QUALITY})
        (build / "verification.json").write_text(json.dumps(
            {"status": "PASS", "sources": [{"publisher": "A"}, {"publisher": "B"}]}))
        self.assertEqual(run_report.collect("shorts")["job_id"], short_id)
        news = run_report.collect("news")
        self.assertEqual(news["job_id"], news_id)
        self.assertEqual(news["research"], {"status": "PASS", "source_count": 2})

    def test_voice_and_visual_fallbacks_are_called_out(self):
        self.job(files={
            "quality_report.json": dict(QUALITY, status="REVIEW", upload_blocked=True,
                                        upload_block_reason="4 of 4 visuals are fallback cards"),
            "narration_synthesis_report.json": {"requested_engine": "edge", "actual_engine": "espeak-ng",
                                                "voice": "espeak-ng offline fallback",
                                                "fallback_reason": "Edge TTS failed"},
            "visual_fallback_report.json": {"visual_count": 4, "fallback_card_count": 4,
                                            "fallback_card_share": 1.0, "status": "FAIL"}})
        report = run_report.collect("shorts", job_status="success")
        self.assertEqual(report["voice"]["status"], "fallback")
        self.assertEqual(report["visuals"]["status"], "fallback_majority")
        self.assertEqual(report["upload"]["status"], "would be blocked")
        markdown = run_report.to_markdown(report)
        self.assertIn("FALLBACK from edge", markdown)
        self.assertIn("4 of 4 are fallback cards", markdown)

    def test_longform_rights_follow_the_release_certificate(self):
        _, build = self.job(fmt="longform", files={"quality_report.json": QUALITY})
        self.assertEqual(run_report.collect("long")["rights"]["status"], "not recorded")
        (build / "release_certificate.json").write_text(json.dumps(
            {"status": "BLOCKED", "blockers": ["Human rights review is incomplete."]}))
        (build / "research_verification_report.json").write_text(json.dumps(
            {"sources": [{}, {}, {}], "claims": [{}, {}]}))
        report = run_report.collect("documentary")
        self.assertEqual(report["rights"]["status"], "blocked")
        self.assertEqual(report["research"]["claim_count"], 2)
        self.assertIn("human claim review required", run_report.to_markdown(report))

    def test_missing_evidence_is_reported_as_missing_not_as_success(self):
        self.job()
        report = run_report.collect("shorts", job_status="success")
        self.assertEqual(report["voice"], {"status": "not recorded"})
        self.assertEqual(report["visuals"], {"status": "not recorded"})
        self.assertEqual(report["qa"]["status"], "not recorded")
        self.assertEqual(report["qa"]["release_gate"], "not evaluated")


class SafetyTests(_Sandbox):
    def test_secrets_in_error_text_are_redacted(self):
        secret = "AIzaSyD-ThisIsNotARealKey_0123456789abcd"
        self.job(status="failed", video=False,
                 error=f"RuntimeError: 403 from https://example.test/v1?key={secret}")
        report = run_report.collect("shorts", job_status="failure")
        dumped = json.dumps(report) + run_report.to_markdown(report) + run_report.annotation(report)
        self.assertNotIn(secret, dumped)

    def test_report_never_copies_environment_values(self):
        self.job(files={"quality_report.json": QUALITY})
        with patch.dict(os.environ, {"YT_REFRESH_TOKEN": "1//refresh-token-value-000",
                                     "GEMINI_API_KEY": "AIzaSyFakeFakeFakeFakeFakeFakeFakeFake00"}):
            report = run_report.collect("shorts", job_status="success")
        dumped = json.dumps(report)
        self.assertNotIn("refresh-token-value", dumped)
        self.assertNotIn("AIzaSyFake", dumped)

    def test_table_cells_cannot_break_the_markdown_table(self):
        job_id, _ = self.job(files={"quality_report.json": QUALITY})
        db.update_video(job_id, title="A | B\nC")
        markdown = run_report.to_markdown(run_report.collect("shorts"))
        self.assertIn("A \\| B C", markdown)


class CommandTests(_Sandbox):
    def test_command_appends_to_the_step_summary_writes_json_and_exits_zero(self):
        from scripts import write_job_summary
        self.job(files={"quality_report.json": QUALITY})
        summary = self.root / "summary.md"
        summary.write_text("earlier content\n")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary), "JOB_STATUS": "success"}):
            code = write_job_summary.main(["--pipeline", "shorts", "--artifact", "shorts-review-1"])
        self.assertEqual(code, 0)
        text = summary.read_text()
        self.assertTrue(text.startswith("earlier content\n"))
        self.assertIn("## Short: BUILT, NOT UPLOADED", text)
        saved = json.loads((self.root / "output" / "run_report.json").read_text())
        self.assertEqual(saved["outcome"], "built")
        self.assertEqual(saved["artifact"], "shorts-review-1")

    def test_a_reporting_error_never_fails_the_workflow(self):
        from scripts import write_job_summary
        summary = self.root / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
                patch("core.run_report.collect", side_effect=RuntimeError("boom")):
            self.assertEqual(write_job_summary.main(["--pipeline", "shorts"]), 0)
        self.assertIn("Job summary unavailable", summary.read_text())


if __name__ == "__main__":
    unittest.main()
