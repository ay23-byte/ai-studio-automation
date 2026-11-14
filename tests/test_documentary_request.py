"""What starts a documentary build, and with which parameters."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from scripts import resolve_documentary_request as request
from scripts.verify_source_run import ALLOWED_EVENTS

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github/workflows/make_longform.yml").read_text(encoding="utf-8")
GOOD = {"topic": "How a nuclear reactor stays under control", "minutes": 18, "language": "english",
        "voice_gender": "female", "presenter_mode": "talking_avatar_auto"}


class ResolveRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "request.json"
        self.file.write_text(json.dumps(GOOD), encoding="utf-8")

    def _pull_request(self, message="Request a build [documentary-run]", same_repository=True):
        return request.resolve("pull_request", {}, self.file, message, same_repository)

    def test_the_form_starts_a_build_with_its_inputs(self):
        result = request.resolve("workflow_dispatch", {"topic": "Tides", "minutes": "22", "semantic_review": "true"},
                                 self.file, "", False)
        self.assertEqual(result["run"], "true")
        self.assertEqual((result["topic"], result["minutes"], result["semantic_review"]), ("Tides", "22", "true"))
        self.assertEqual(result["language"], "hinglish")           # the form's default

    def test_a_marked_pull_request_commit_starts_a_production_test(self):
        result = self._pull_request()
        self.assertEqual(result["run"], "true")
        self.assertEqual(result["minutes"], "18")
        self.assertEqual(result["voice_gender"], "female")
        self.assertIn("cannot be released", result["reason"])

    def test_an_ordinary_push_to_the_pull_request_builds_nothing(self):
        result = self._pull_request(message="Fix a typo")
        self.assertEqual(result, {"run": "false", "reason": result["reason"]})
        self.assertIn("[documentary-run]", result["reason"])

    def test_a_fork_never_starts_a_build(self):
        self.assertEqual(self._pull_request(same_repository=False)["run"], "false")

    def test_other_events_build_nothing(self):
        self.assertEqual(request.resolve("push", {}, self.file, "[documentary-run]", True)["run"], "false")

    def test_values_outside_the_form_choices_are_refused(self):
        for field, value in (("minutes", 45), ("language", "french"), ("presenter_mode", "deepfake"),
                             ("visual_mode", "slideshow"), ("voice_gender", "robot"), ("category", "x; rm -rf /")):
            self.file.write_text(json.dumps({**GOOD, field: value}), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(request.RequestError):
                self._pull_request()

    def test_topic_is_required_single_line_and_bounded(self):
        self.file.write_text(json.dumps({**GOOD, "topic": "  "}), encoding="utf-8")
        with self.assertRaises(request.RequestError):
            self._pull_request()
        self.file.write_text(json.dumps({**GOOD, "topic": "x" * 201}), encoding="utf-8")
        with self.assertRaises(request.RequestError):
            self._pull_request()
        self.file.write_text(json.dumps({**GOOD, "topic": "Reactors\nrun=true"}), encoding="utf-8")
        self.assertEqual(self._pull_request()["topic"], "Reactors run=true")   # cannot add an output line

    def test_a_missing_or_broken_request_file_is_an_error_not_a_silent_skip(self):
        self.file.write_text("{not json", encoding="utf-8")
        with self.assertRaises(request.RequestError):
            self._pull_request()
        self.file.unlink()
        with self.assertRaises(request.RequestError):
            self._pull_request()

    def test_main_writes_outputs_and_reports_a_bad_request(self):
        import io, os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        outputs = Path(self.temp.name) / "out.txt"
        env = {"EVENT_NAME": "pull_request", "SAME_REPOSITORY": "true", "HEAD_COMMIT_MESSAGE": "go [documentary-run]",
               "GITHUB_OUTPUT": str(outputs)}
        with patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
            self.assertEqual(request.main(["--request", str(self.file)]), 0)
        written = dict(line.split("=", 1) for line in outputs.read_text().splitlines())
        self.assertEqual(written["run"], "true")
        self.assertEqual(written["topic"], GOOD["topic"])
        self.file.write_text(json.dumps({**GOOD, "minutes": 5}), encoding="utf-8")
        captured = io.StringIO()
        with patch.dict(os.environ, env), redirect_stdout(captured):
            self.assertEqual(request.main(["--request", str(self.file)]), 1)
        self.assertIn("::error title=Documentary request::minutes must be one of", captured.getvalue())


class WorkflowContractTests(unittest.TestCase):
    def test_form_choices_and_script_choices_are_the_same(self):
        for field, allowed in request.CHOICES.items():
            block = WORKFLOW[WORKFLOW.index(f"      {field}:\n"):]
            options = re.search(r"options: \[(.*?)\]", block).group(1)
            self.assertEqual([item.strip().strip('"') for item in options.split(",")], allowed, field)
        check = re.search(r'"\$INPUT_MINUTES" =~ \^\((.*?)\)\$', WORKFLOW).group(1).split("|")
        self.assertEqual(check, request.CHOICES["minutes"])

    def test_the_build_job_runs_only_when_the_request_says_so(self):
        build = WORKFLOW[WORKFLOW.index("  build:\n"):]
        self.assertIn("needs: request", build)
        self.assertIn("if: needs.request.outputs.run == 'true'", build)
        self.assertNotIn("inputs.", build)

    def test_the_pull_request_trigger_is_limited_to_the_request_file(self):
        trigger = WORKFLOW[WORKFLOW.index("  pull_request:"):WORKFLOW.index("\npermissions:")]
        self.assertIn(f'paths: ["{request.REQUEST_FILE}"]', trigger)
        self.assertIn("branches: [main]", trigger)

    def test_a_pull_request_build_can_never_be_released(self):
        self.assertNotIn("pull_request", ALLOWED_EVENTS)

    def test_the_workflow_still_does_not_upload(self):
        self.assertNotIn("YT_REFRESH_TOKEN", WORKFLOW)
        self.assertIn('UPLOAD_REQUESTED: "false"', WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
