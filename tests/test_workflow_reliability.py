"""Phase 2 reliability guards: workflow hygiene, state-branch credentials, model profiles, alerts."""
import base64
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))

# Majors that run on Node 24.  Node 20 actions are deprecated on hosted runners.
NODE24_MAJORS = {"actions/checkout": 5, "actions/setup-python": 6, "actions/upload-artifact": 6,
                 "actions/download-artifact": 7, "actions/github-script": 8, "actions/setup-node": 5,
                 "actions/cache": 5}
# The self-hosted Windows runner version cannot be verified from the repository.
SELF_HOSTED = {"openvino-ltx-local-gpu.yml"}
PRODUCTION = {"make_shorts.yml": "shorts", "daily-news-auto-short.yml": "news_short", "make_longform.yml": "longform"}


def _text(name):
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def _steps(text):
    """Split a workflow into step blocks (indentation based, no YAML dependency)."""
    blocks, current = [], None
    for line in text.splitlines():
        if re.match(r"^      - ", line):
            if current is not None:
                blocks.append("\n".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current))
    return blocks


class ActionVersionTests(unittest.TestCase):
    def test_hosted_workflows_use_node24_action_majors(self):
        pattern = re.compile(r"uses:\s*(actions/[a-z-]+)@v(\d+)")
        for path in WORKFLOWS:
            if path.name in SELF_HOSTED:
                continue
            for action, major in pattern.findall(path.read_text(encoding="utf-8")):
                with self.subTest(workflow=path.name, action=action):
                    self.assertGreaterEqual(int(major), NODE24_MAJORS.get(action, 0),
                                            f"{action}@v{major} still targets Node 20")

    def test_upload_and_download_artifact_majors_are_compatible(self):
        # Artifacts written by upload-artifact v4+ need download-artifact v4+.
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            for major in re.findall(r"actions/download-artifact@v(\d+)", text):
                self.assertGreaterEqual(int(major), 4, path.name)

    def test_github_script_blocks_do_not_use_the_removed_require(self):
        # actions/github-script v9 removed require('@actions/github').
        for path in WORKFLOWS:
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"require\(['\"]@actions/github['\"]\)", path.name)


class WorkflowStructureTests(unittest.TestCase):
    def test_every_job_has_a_timeout(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            jobs = re.findall(r"^    runs-on:", text, flags=re.M)
            timeouts = re.findall(r"^    timeout-minutes:\s*\d+", text, flags=re.M)
            with self.subTest(workflow=path.name):
                self.assertTrue(jobs)
                self.assertEqual(len(jobs), len(timeouts), "a job has no timeout-minutes")

    def test_no_workflow_pins_a_gemini_model_id(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"gemini-\d",
                                    "model ids belong in core/gemini.py or a repository variable")

    def test_model_variables_are_read_from_repository_variables(self):
        for path in WORKFLOWS:
            for line in path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s+(GEMINI_MODEL|GEMINI_FALLBACK_MODELS):\s*(.*)$", line)
                if match:
                    with self.subTest(workflow=path.name, variable=match.group(1)):
                        self.assertRegex(match.group(2), r"^\$\{\{ vars\.[A-Z_]+ \}\}$")

    def test_longform_workflows_select_the_longform_profile(self):
        for name in ("make_longform.yml", "manual-live-documentary-e2e.yml", "wan21-documentary-rerender.yml"):
            text = _text(name)
            self.assertIn("GEMINI_PROFILE: longform", text, name)
            self.assertIn("vars.GEMINI_MODEL_LONGFORM", text, name)

    def test_production_workflows_report_and_alert(self):
        for name, pipeline in PRODUCTION.items():
            steps = _steps(_text(name))
            names = [re.search(r"(?:name|uses):\s*(.+)", block).group(1) for block in steps]
            summary = next((b for b in steps if "scripts/write_job_summary.py" in b), None)
            alert = next((b for b in steps if "scripts/alert_on_failure.py" in b), None)
            artifact = next((b for b in steps if "actions/upload-artifact@" in b), None)
            with self.subTest(workflow=name):
                self.assertIsNotNone(summary, "no job summary step")
                self.assertIn("if: always()", summary)                # also after a failure
                self.assertIn("JOB_STATUS: ${{ job.status }}", summary)
                self.assertIn(f"--pipeline {pipeline}", summary)
                self.assertIsNotNone(alert, "no failure alert step")
                self.assertIn("if: failure()", alert)
                self.assertIn("if: always()", artifact)
                self.assertIn("output/run_report.json", artifact)     # the report is kept with the artifact
                self.assertLess(steps.index(summary), steps.index(artifact), names)
                self.assertLess(steps.index(artifact), steps.index(alert), names)

    def test_scheduled_builds_use_bounded_recovery(self):
        for name in ("make_shorts.yml", "daily-news-auto-short.yml"):
            text = _text(name)
            self.assertIn("scripts/build_with_recovery.py", text, name)
            self.assertIn("BUILD_RECOVERY_ATTEMPTS", text, name)
        # The news workflow uploads inside the build command, so a recovered job must too.
        self.assertIn("--upload-after-recovery", _text("daily-news-auto-short.yml"))

    def test_state_is_saved_even_when_the_build_fails(self):
        for name in ("make_shorts.yml", "daily-news-auto-short.yml"):
            save = next(b for b in _steps(_text(name)) if "persist_state.py save" in b)
            self.assertIn("if: always()", save, name)

    def test_scripts_referenced_by_workflows_exist(self):
        for path in WORKFLOWS:
            for script in re.findall(r"python (scripts/[a-z_]+\.py)", path.read_text(encoding="utf-8")):
                self.assertTrue((ROOT / script).is_file(), f"{path.name} runs missing {script}")


@unittest.skipUnless(shutil.which("git"), "git is required")
class StateBranchCredentialTests(unittest.TestCase):
    """The temporary clone must get the checkout's credential without it touching argv."""
    HOST = "https://github.com/"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "checkout"
        self.other = self.root / "temp-clone"
        for directory in (self.repo, self.other):
            directory.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                       cwd=self.repo, check=True)
        clean = {key: "" for key in ("STATE_PUSH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")}
        for item in (patch.dict(os.environ, clean),):
            item.start()
            self.addCleanup(item.stop)

    def auth_env(self):
        from scripts import persist_state
        with patch.object(persist_state, "ROOT", self.repo):
            return persist_state._auth_env("origin")

    def header_seen_by_temp_clone(self, env):
        result = subprocess.run(["git", "config", "--get-all", f"http.{self.HOST}.extraheader"],
                                cwd=self.other, env={**os.environ, **env}, capture_output=True, text=True)
        return result.stdout.strip()

    def test_checkout_v4_layout_header_in_git_config(self):
        subprocess.run(["git", "config", "--local", f"http.{self.HOST}.extraheader",
                        "AUTHORIZATION: basic djQtdG9rZW4="], cwd=self.repo, check=True)
        self.assertEqual(self.header_seen_by_temp_clone(self.auth_env()), "AUTHORIZATION: basic djQtdG9rZW4=")

    def test_checkout_v6_layout_header_in_a_separate_included_file(self):
        credentials = self.root / "runner-temp" / "git-credentials-1234.config"
        credentials.parent.mkdir()
        credentials.write_text(f'[http "{self.HOST}"]\n\textraheader = AUTHORIZATION: basic djYtdG9rZW4=\n')
        subprocess.run(["git", "config", "--local", f"includeIf.gitdir:{self.repo}/.git.path", str(credentials)],
                       cwd=self.repo, check=True)
        # The includeIf condition does not match the temporary clone on its own ...
        self.assertEqual(self.header_seen_by_temp_clone({}), "")
        # ... so the credentials file is included directly.
        env = self.auth_env()
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "include.path")
        self.assertEqual(self.header_seen_by_temp_clone(env), "AUTHORIZATION: basic djYtdG9rZW4=")

    def test_explicit_token_wins_and_is_never_placed_on_a_command_line(self):
        from scripts import persist_state
        token = "ghs_ExampleTokenValue0123456789abcdef"
        calls = []
        real = subprocess.run

        def spy(command, *args, **kwargs):
            calls.append(list(command))
            return real(command, *args, **kwargs)

        with patch.dict(os.environ, {"GITHUB_TOKEN": token}), patch.object(persist_state, "ROOT", self.repo), \
                patch.object(persist_state.subprocess, "run", side_effect=spy):
            env = persist_state._auth_env("origin")
        expected = "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertEqual(self.header_seen_by_temp_clone(env), expected)
        flat = " ".join(" ".join(command) for command in calls)
        self.assertNotIn(token, flat)
        self.assertNotIn(expected.split()[-1], flat)

    def test_no_credential_means_no_extra_environment(self):
        self.assertEqual(self.auth_env(), {})

    def test_git_errors_are_scrubbed_before_printing(self):
        from scripts import persist_state
        text = persist_state._scrub("fatal: unable to access 'https://x-access-token:ghs_abcdefghijklmnopqrstuvwxyz0123@"
                                    "github.com/o/r/': AUTHORIZATION: basic QUJDREVGRw== denied")
        self.assertNotIn("ghs_abcdefghijklmnopqrstuvwxyz0123", text)
        self.assertNotIn("QUJDREVGRw==", text)

    def test_unreachable_remote_is_an_error_not_an_empty_state(self):
        """A network failure must not look like 'no state branch yet' (that would disable dedupe)."""
        from scripts import persist_state
        subprocess.run(["git", "remote", "set-url", "origin", str(self.root / "does-not-exist.git")],
                       cwd=self.repo, check=True)
        with patch.object(persist_state, "ROOT", self.repo), patch.object(persist_state.time, "sleep"):
            with self.assertRaises(RuntimeError) as caught:
                persist_state._remote_has_branch("origin", "automation-state")
            self.assertIn("Could not query", str(caught.exception))
            # Captured: on a runner an "::error::" line printed by a test would be shown
            # as a failure annotation on a passing check.
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(persist_state.main(["restore"]), 1)
            self.assertIn("::error::Automation state restore failed", printed.getvalue())

    def test_missing_branch_on_a_reachable_remote_is_a_clean_empty_start(self):
        from scripts import persist_state
        bare = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        subprocess.run(["git", "remote", "set-url", "origin", str(bare)], cwd=self.repo, check=True)
        with patch.object(persist_state, "ROOT", self.repo):
            self.assertFalse(persist_state._remote_has_branch("origin", "automation-state"))
            self.assertEqual(persist_state.main(["restore"]), 0)


class GeminiProfileTests(unittest.TestCase):
    def test_default_profile_is_unchanged(self):
        from core import gemini
        self.assertEqual(gemini.model_chain(environ={}), [gemini.DEFAULT_MODEL, *gemini.DEFAULT_FALLBACK_MODELS])

    def test_longform_profile_keeps_the_order_the_documentary_workflows_used(self):
        from core import gemini
        chain = gemini.model_chain(environ={"GEMINI_PROFILE": "longform", "GEMINI_MODEL": "",
                                            "GEMINI_FALLBACK_MODELS": ""})
        self.assertEqual(chain, ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash",
                                 "gemini-3.7-flash", "gemini-3.8-flash"])

    def test_explicit_variables_override_the_profile(self):
        from core import gemini
        chain = gemini.model_chain(environ={"GEMINI_PROFILE": "longform", "GEMINI_MODEL": "gemini-9.9-pro",
                                            "GEMINI_FALLBACK_MODELS": "none"})
        self.assertEqual(chain, ["gemini-9.9-pro"])

    def test_unknown_profile_falls_back_to_default_and_is_reported(self):
        from core import gemini
        env = {"GEMINI_PROFILE": "documentry"}
        self.assertEqual(gemini.profile_name(env), "default")
        self.assertEqual(gemini.unknown_profile(env), "documentry")
        self.assertIsNone(gemini.unknown_profile({"GEMINI_PROFILE": "longform"}))
        self.assertEqual(gemini.model_chain(environ=env)[0], gemini.DEFAULT_MODEL)


class HealthCheckTests(unittest.TestCase):
    """`run.py doctor` rows added in Phase 1 (model configuration, narration provider)."""

    def checks(self, **patches):
        from core import config, health
        active = [patch.object(config, name, value) for name, value in patches.items()]
        for item in active:
            item.start()
        try:
            return {row["check"]: row for row in health.run_health_check()["checks"]}
        finally:
            for item in active:
                item.stop()

    def test_valid_model_chain_passes_and_names_its_source(self):
        with patch.dict(os.environ, {"GEMINI_MODEL": "", "GEMINI_PROFILE": ""}):
            row = self.checks(GEMINI_MODEL="gemini-a", GEMINI_FALLBACK_MODELS=["gemini-b"])["Gemini model configuration"]
        self.assertEqual(row["status"], "PASS")
        self.assertIn("primary gemini-a, fallbacks gemini-b", row["detail"])
        self.assertIn("built-in 'default' defaults", row["detail"])
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-a"}):
            row = self.checks(GEMINI_MODEL="gemini-a", GEMINI_FALLBACK_MODELS=[])["Gemini model configuration"]
        self.assertIn("no fallback model", row["detail"])
        self.assertIn("GEMINI_MODEL override", row["detail"])

    def test_invalid_model_id_fails_the_check(self):
        row = self.checks(GEMINI_MODEL="gemini-a", GEMINI_FALLBACK_MODELS=["bad id!"])["Gemini model configuration"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("bad id!", row["detail"])

    def test_unknown_profile_is_a_warning(self):
        with patch.dict(os.environ, {"GEMINI_PROFILE": "documentry"}):
            rows = self.checks()
        self.assertEqual(rows["Gemini model profile"]["status"], "WARN")
        with patch.dict(os.environ, {"GEMINI_PROFILE": "longform"}):
            self.assertNotIn("Gemini model profile", self.checks())

    def test_narration_provider_levels(self):
        from core import voice
        cases = (({"edge": True, "chatterbox": False, "espeak-ng": False}, "PASS"),
                 ({"edge": False, "chatterbox": False, "espeak-ng": True}, "WARN"),
                 ({"edge": False, "chatterbox": False, "espeak-ng": False}, "FAIL"))
        for status, expected in cases:
            with self.subTest(status=status), patch.object(voice, "provider_status", return_value=status):
                self.assertEqual(self.checks()["narration provider"]["status"], expected)
        with patch.object(voice, "provider_status", side_effect=RuntimeError("broken install")):
            row = self.checks()["narration provider"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("broken install", row["detail"])


class QualityLogTests(unittest.TestCase):
    def test_final_score_is_printed_after_the_fallback_deduction(self):
        import contextlib
        import io
        from core import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality_report.json"
            report.write_text(json.dumps({"status": "PASS", "score": 92, "warnings": []}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                gated = pipeline._apply_fallback_gate(report, {
                    "fallback_card_count": 4, "visual_count": 4, "status": "FAIL", "max_share_allowed": 0.5})
            self.assertEqual(gated["score"], 72)
            self.assertEqual(json.loads(report.read_text())["score"], 72)
            self.assertIn("REVIEW (72/100)", output.getvalue())


class FailureAlertTests(unittest.TestCase):
    ENV = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42"}

    def test_message_states_the_reason_and_the_run(self):
        from scripts import alert_on_failure
        with patch.dict(os.environ, self.ENV):
            text = alert_on_failure.build_message("make-shorts", {
                "outcome": "failed", "topic": "Why ice floats", "job_id": 7,
                "failure_reason": "FFmpegError: render crashed"})
        self.assertTrue(text.startswith("❌ AI Studio make-shorts failed"))
        self.assertIn("Why ice floats (job #7)", text)
        self.assertIn("Reason: FFmpegError: render crashed", text)
        self.assertTrue(text.endswith("https://github.com/o/r/actions/runs/42"))

    def test_blocked_upload_and_post_upload_failure_are_not_called_a_build_failure(self):
        from scripts import alert_on_failure
        blocked = alert_on_failure.build_message("news", {"outcome": "upload_blocked", "failure_reason": "gate"})
        self.assertIn("built but NOT uploaded", blocked)
        after = alert_on_failure.build_message("news", {
            "outcome": "uploaded", "upload": {"url": "https://youtu.be/x", "privacy": "private"}})
        self.assertIn("uploaded, but a later step failed", after)
        self.assertIn("https://youtu.be/x (private)", after)

    def test_missing_report_still_alerts_and_the_command_never_fails(self):
        from scripts import alert_on_failure
        self.assertIn("No run report was written", alert_on_failure.build_message("x", None))
        with tempfile.TemporaryDirectory() as tmp, patch("core.notify.send") as send:
            self.assertEqual(alert_on_failure.main(["--name", "x", "--report", str(Path(tmp) / "none.json")]), 0)
            send.assert_called_once()
        with patch("core.notify.send", side_effect=RuntimeError("network")):
            self.assertEqual(alert_on_failure.main(["--name", "x"]), 0)


class DocumentaryUploadFailureRecordTests(unittest.TestCase):
    def test_failure_reason_is_written_for_the_summary_and_read_by_the_report(self):
        from core import run_report
        from scripts import upload_documentary_artifact as uploader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = ["upload_documentary_artifact.py", "--artifact-dir", str(root), "--source-run-id", "123"]
            with patch("sys.argv", argv), patch.object(
                    uploader, "upload_automated_artifact",
                    side_effect=ValueError("Asset licence register must cover every shot exactly once.")):
                self.assertEqual(uploader.main(), 1)
            record = json.loads((root / "youtube-upload-failure.json").read_text())
            self.assertEqual(record["error_type"], "ValueError")
            section = run_report._upload_section({}, root, {}, {}, None, root / "youtube-upload-failure.json")
            self.assertEqual(section["status"], "blocked")
            self.assertIn("licence register", section["reason"])
            # A later success removes the stale failure record.
            with patch("sys.argv", argv), patch.object(uploader, "upload_automated_artifact", return_value={}):
                self.assertEqual(uploader.main(), 0)
            self.assertFalse((root / "youtube-upload-failure.json").exists())

    def test_documentary_uploader_result_is_reported_without_claiming_a_privacy_read_back(self):
        from core import run_report
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "youtube-upload-result.json"
            result.write_text(json.dumps({"video_id": "vid12345678", "url": "https://www.youtube.com/watch?v=vid12345678",
                                          "privacy_status": "private", "processing_status": "ok"}))
            section = run_report._upload_section({}, Path(tmp), {}, {}, result, None)
        self.assertEqual(section["status"], "uploaded")
        self.assertEqual(section["privacy"], "private")
        self.assertIsNone(section["privacy_verified"])


if __name__ == "__main__":
    unittest.main()
