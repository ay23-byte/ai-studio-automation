"""Source-run verification, package discovery and the build-side packaging step."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from core import review_contract as contract
from scripts import package_build_for_review as build_step
from scripts import release_review_package as release
from scripts import verify_source_run as source_run
from tests.review_package_fixture import approve, fake_media, make_package

REPO = "ay23-byte/ai-studio-automation"
ROOT = Path(__file__).resolve().parents[1]


def run(**changes):
    data = {"id": 77, "name": "Make Shorts", "path": ".github/workflows/make_shorts.yml", "status": "completed",
            "conclusion": "success", "event": "workflow_dispatch", "head_branch": "main", "head_sha": "a" * 40,
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}
    data.update(changes)
    return data


def quiet(function, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = function(*args, **kwargs)
    return result, buffer.getvalue()


class SourceRunTests(unittest.TestCase):
    def problems(self, mode="shortform", ref="main", **changes):
        return source_run.problems_with(run(**changes), repository=REPO, mode=mode, ref=ref, run_id="77")

    def test_successful_build_of_the_right_mode_is_accepted(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.problems(mode="longform", path=".github/workflows/make_longform.yml"), [])
        self.assertEqual(self.problems(mode="daily_news", event="schedule",
                                       path=".github/workflows/daily-news-auto-short.yml"), [])

    def test_every_wrong_source_is_refused(self):
        cases = (
            ({"conclusion": "failure"}, "did not succeed"),
            ({"conclusion": "cancelled"}, "did not succeed"),
            ({"status": "in_progress", "conclusion": None}, "has not finished"),
            ({"path": ".github/workflows/validate.yml"}, "not a shortform build workflow"),
            ({"path": ".github/workflows/make_longform.yml"}, "not a shortform build workflow"),
            ({"path": ".github/workflows/release_shorts_private.yml"}, "not a shortform build workflow"),
            ({"repository": {"full_name": "someone/else"}}, "not ay23-byte/ai-studio-automation"),
            ({"head_repository": {"full_name": "fork/ai-studio-automation"}}, "from a fork"),
            ({"head_branch": "experiment"}, "same branch"),
            ({"event": "pull_request_target"}, "not an accepted trigger"),
            ({"id": 78}, "different run"),
        )
        for changes, fragment in cases:
            with self.subTest(changes=changes):
                self.assertIn(fragment, " ".join(self.problems(**changes)))

    def test_cli_refuses_non_numeric_ids_and_missing_runs_without_guessing(self):
        code, output = quiet(source_run.main, ["--run-id", "latest", "--mode", "shortform", "--repository", REPO])
        self.assertEqual(code, 2)
        self.assertIn("never 'latest'", output)
        missing = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), patch.object(source_run, "fetch_run", side_effect=missing):
            code, output = quiet(source_run.main, ["--run-id", "5", "--mode", "shortform", "--repository", REPO])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", output)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), \
                patch.object(source_run, "fetch_run", side_effect=urllib.error.URLError("down")):
            code, _ = quiet(source_run.main, ["--run-id", "5", "--mode", "shortform", "--repository", REPO])
        self.assertEqual(code, 1)                      # unreadable is an error, never an acceptance

    def test_cli_accepts_and_reports(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), patch.object(source_run, "fetch_run", return_value=run()):
            code, output = quiet(source_run.main, ["--run-id", "77", "--mode", "shortform", "--ref", "main",
                                                   "--repository", REPO])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["accepted"])
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}), \
                patch.object(source_run, "fetch_run", return_value=run(conclusion="failure")):
            code, output = quiet(source_run.main, ["--run-id", "77", "--mode", "shortform", "--ref", "main",
                                                   "--repository", REPO])
        self.assertEqual(code, 2)
        self.assertIn("::error::The build run did not succeed", output)

    def test_every_listed_build_workflow_exists(self):
        for mode, paths in source_run.BUILD_WORKFLOWS.items():
            self.assertIn(mode, contract.MODES)
            for path in paths:
                self.assertTrue((ROOT / path).is_file(), path)


class FindPackagesTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_finds_packages_by_content_and_ignores_other_manifests(self):
        artifact = self.root / "artifact"
        package = artifact / "shorts-review-1" / "build" / "job_3" / "anything"
        package.parent.mkdir(parents=True)
        make_package(package, "shortform")
        render = artifact / "shorts-review-1" / "rendered"
        render.mkdir()
        (render / "production_manifest.json").write_text(json.dumps({"shots": []}))    # renderer manifest
        (render / "video.mp4").write_bytes(b"x")
        old = artifact / "v1"
        old.mkdir()
        (old / "production_manifest.json").write_text(json.dumps({"schema_version": 1, "mode": "shortform"}))
        (old / "video.mp4").write_bytes(b"x")
        (artifact / "broken").mkdir()
        (artifact / "broken" / "production_manifest.json").write_text("{not json")
        self.assertEqual(contract.find_packages(artifact), [package])
        self.assertEqual(contract.find_packages(self.root / "missing"), [])

    def test_release_cli_refuses_zero_or_several_packages(self):
        artifact = self.root / "artifact"
        artifact.mkdir()
        args = ["--decisions", str(self.root / "d.json"), "--video-sha256", "a" * 64, "--confirm-private-upload"]
        code, output = quiet(release.main, ["--artifact-root", str(artifact)] + args)
        self.assertEqual(code, 2)
        self.assertIn("found 0", output)
        make_package(artifact / "one", "shortform")
        make_package(artifact / "two", "shortform")
        code, output = quiet(release.main, ["--artifact-root", str(artifact)] + args)
        self.assertEqual(code, 2)
        self.assertIn("found 2", output)
        self.assertIn("Nothing was uploaded", output)

    def test_release_cli_uses_the_single_package_it_finds(self):
        artifact = self.root / "artifact"
        package = artifact / "run" / "pkg"
        package.parent.mkdir(parents=True)
        manifest = make_package(package, "daily_news")
        decisions = self.root / "decisions.json"
        decisions.write_text(json.dumps(approve(package)))
        seen = {}

        def fake_release(package_dir, *args, **kwargs):
            seen["dir"] = Path(package_dir)
            return {"upload": {"url": "https://youtu.be/x", "privacy_status_read_back": "private",
                               "channel_title": "Engineering Physics"}}
        with patch.object(release, "release_package", side_effect=fake_release):
            code, _ = quiet(release.main, ["--artifact-root", str(artifact), "--decisions", str(decisions),
                                           "--video-sha256", manifest["video"]["sha256"], "--mode", "daily_news",
                                           "--source-run-id", "4242", "--confirm-private-upload"])
        self.assertEqual(code, 0)
        self.assertEqual(seen["dir"], package)


class BuildStepTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.build_root = self.root / "build"
        self.output_root = self.root / "output"
        self.build_root.mkdir()
        self.output_root.mkdir()
        self.summary_file = self.root / "summary.md"

    def call(self, *extra, mode="shortform"):
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary_file), "GITHUB_RUN_ID": "555"}):
            return quiet(build_step.main, ["--mode", mode, "--build-root", str(self.build_root),
                                           "--output-root", str(self.output_root), *extra])

    def test_nothing_built_is_an_error_unless_explicitly_allowed(self):
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("::error::No video was built", output)
        code, output = self.call("--allow-no-build", mode="daily_news")
        self.assertEqual(code, 0)
        self.assertNotIn("::error::", output)
        self.assertIn("nothing to review", self.summary_file.read_text())

    def test_two_builds_or_an_unfinished_build_are_refused(self):
        (self.build_root / "job_1").mkdir()
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("no rendered video (shorts_1.mp4)", output)
        (self.build_root / "job_2").mkdir()
        code, output = self.call()
        self.assertEqual(code, 1)
        self.assertIn("Refusing to guess", output)

    def _short_build(self, quality="PASS"):
        build = self.build_root / "job_9"
        build.mkdir()
        (build / "img0.jpg").write_bytes(b"image")
        (build / "img0.jpg.source.json").write_text(json.dumps({
            "provider": "NASA", "page_url": "https://images.nasa.gov/x", "licence": "Public Domain",
            "licence_url": "https://www.nasa.gov/media-guidelines", "attribution_text": "NASA"}))
        (build / "asset_manifest.json").write_text(json.dumps({"assets": [{"scene": 1, "file": "img0.jpg"}]}))
        (build / "subs.ass").write_text("[Events]\n")
        (build / "script.json").write_text(json.dumps(
            {"title": "A test", "language": "english", "scenes": [{"say": "One line.", "image": "A thing"}]}))
        (build / "quality_report.json").write_text(json.dumps({"status": quality}))
        (build / "narration_synthesis_report.json").write_text(json.dumps(
            {"requested_engine": "edge", "actual_engine": "edge", "voice": "en-GB-RyanNeural"}))
        (self.output_root / "shorts_9.mp4").write_bytes(b"\0" * 4096)
        return build

    def test_packages_the_single_build_and_explains_the_next_step(self):
        build = self._short_build()
        with fake_media("shortform"):
            code, _ = self.call()
        self.assertEqual(code, 0)
        package = build / "shared_review_package"
        self.assertEqual(contract.find_packages(self.root), [package])
        text = self.summary_file.read_text()
        self.assertIn("Awaiting human review. Nothing was uploaded.", text)
        self.assertIn("`source_run_id` = `555`", text)
        self.assertIn("Release approved Short (Private)", text)
        self.assertIn(json.loads((package / "production_manifest.json").read_text())["video"]["sha256"], text)

    def test_failed_qa_fails_the_step_but_keeps_the_package(self):
        build = self._short_build(quality="FAIL")
        with fake_media("shortform"):
            code, output = self.call()
        self.assertEqual(code, 2)
        self.assertIn("::error::The review package fails automated QA", output)
        self.assertTrue((build / "shared_review_package" / "review_dashboard.html").is_file())
        text = self.summary_file.read_text()
        self.assertIn("NOT APPROVABLE", text)
        self.assertNotIn("How to review and release", text)


class ScriptEntryPointTests(unittest.TestCase):
    """Workflows run ``python scripts/<name>.py`` from the checkout; nothing sets PYTHONPATH."""

    def test_every_script_starts_from_a_foreign_working_directory(self):
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertGreater(len(scripts), 25)
        failures = []
        with tempfile.TemporaryDirectory() as temp:
            env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
            env.update({"AI_STUDIO_DB_PATH": str(Path(temp) / "db.sqlite"), "AI_STUDIO_OUTPUT_DIR": temp,
                        "AI_STUDIO_BUILD_DIR": temp, "AI_STUDIO_STATE_DIR": temp})
            for script in scripts:
                if script.name == "__init__.py":
                    continue
                result = subprocess.run([sys.executable, str(script), "--help"], cwd=temp, env=env,
                                        capture_output=True, text=True, timeout=120)
                if result.returncode != 0:
                    failures.append(f"{script.name}: {result.stderr.strip().splitlines()[-1:]}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
