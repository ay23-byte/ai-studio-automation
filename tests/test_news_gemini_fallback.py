"""News-script writer: model fallback, credentials, malformed output (HTTP mocked).

The file name matches ``test_*news*.py`` on purpose so the lightweight daily
collection workflow runs it too.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core import gemini
from core import news_shorts_writer as writer

INVALID_KEY_BODY = '{"error":{"code":400,"message":"API key not valid.","details":[{"reason":"API_KEY_INVALID"}]}}'

PACKET = {
    "story_id": "orbital-test-1",
    "story_title": "Agency confirms successful engine test",
    "review_status": "approved",
    "story_type": "breaking",
    "language": "english",
    "sources": [{"source_id": "S1", "publisher": "Space Agency", "url": "https://example.org/a"}],
    "verified_facts": [{"fact": "The agency completed an engine test.", "source_ids": ["S1"],
                        "evidence_note": "Stated in the agency release."}],
}


def _scenes(count=5, words=18):
    say = " ".join(["word"] * words)
    return [{"say": say, "caption": "caption", "label": "L", "image": "illustration",
             "keywords": ["k"], "source_ids": ["S1"]} for _ in range(count)]


def _draft(scenes=None):
    return {"title": "Engine test", "hook": "h", "category": "science", "format": "shorts",
            "estimated_seconds": 45, "sources": ["S1"],
            "claims": [{"claim": "tested", "source_ids": ["S1"]}],
            "scenes": scenes if scenes is not None else _scenes()}


def _ok(payload=None, raw=None):
    response = Mock(status_code=200, headers={}, text="")
    response.raise_for_status.return_value = None
    text = raw if raw is not None else json.dumps(payload if payload is not None else _draft())
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return response


def _error(status, body="", headers=None):
    response = Mock(status_code=status, headers=headers or {}, text=body)
    response.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}", response=response)
    return response


class NewsWriterFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = Path(self.tmp.name) / "packet.json"
        self.packet.write_text(json.dumps(PACKET), encoding="utf-8")
        self.out = Path(self.tmp.name) / "script.json"
        patches = [
            patch.dict("os.environ", {"GEMINI_API_KEY": "secret-test-key", "GEMINI_MODEL": "gemini-primary",
                                      "GEMINI_FALLBACK_MODELS": "gemini-second,gemini-third"}),
            # The source gate has its own tests; here it is satisfied so the
            # request/fallback logic that follows it can be exercised.
            patch.object(writer, "verify_packet", return_value={"safe_to_draft": True, "fact_checks": []}),
            patch.object(writer.time, "sleep"),
            patch.object(gemini, "_log_path", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.sleep = writer.time.sleep

    def _run(self, responses):
        with patch.object(writer.requests, "post", side_effect=responses) as post:
            try:
                return writer.generate_reviewed_shorts(self.packet, self.out), post
            except Exception as exc:  # returned so tests can assert on calls too
                return exc, post

    @staticmethod
    def _models(post):
        return [call.args[0].split("/models/")[1].split(":")[0] for call in post.call_args_list]

    def test_primary_success_records_the_model(self):
        script, post = self._run([_ok()])
        self.assertEqual(self._models(post), ["gemini-primary"])
        self.assertEqual(script["generation"], {
            "provider": "gemini", "model": "gemini-primary", "configured_model": "gemini-primary",
            "attempted_models": ["gemini-primary"], "fallback_used": False})
        saved = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(saved["generation"]["model"], "gemini-primary")
        self.assertEqual(saved["editorial_status"], "draft_requires_human_script_and_visual_review")
        self.assertNotIn("secret-test-key", self.out.read_text(encoding="utf-8"))

    def test_overloaded_primary_falls_back_to_the_next_model(self):
        script, post = self._run([_error(503, "overloaded")] * 3 + [_ok()])
        self.assertEqual(self._models(post), ["gemini-primary"] * 3 + ["gemini-second"])
        self.assertEqual(script["generation"]["model"], "gemini-second")
        self.assertTrue(script["generation"]["fallback_used"])
        self.assertEqual(gemini.last_usage("news_script")["model"], "gemini-second")

    def test_rate_limit_and_each_server_error_fall_back(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                script, post = self._run([_error(status, "x")] * 3 + [_ok()])
                self.assertEqual(self._models(post)[-1], "gemini-second")
                self.assertEqual(script["generation"]["model"], "gemini-second")

    def test_timeouts_are_retried_then_fall_back(self):
        script, post = self._run([requests.Timeout("timed out")] * 3 + [_ok()])
        self.assertEqual(self._models(post), ["gemini-primary"] * 3 + ["gemini-second"])
        self.assertEqual(script["generation"]["model"], "gemini-second")

    def test_retired_primary_is_skipped_at_once_and_not_asked_again(self):
        short = _draft(_scenes(count=5, words=4))          # 20 words: fails validation
        script, post = self._run([_error(404, "not found"), _ok(short), _ok()])
        # The second draft goes straight to the model that answered.
        self.assertEqual(self._models(post), ["gemini-primary", "gemini-second", "gemini-second"])
        self.sleep.assert_not_called()
        self.assertEqual(script["generation"]["model"], "gemini-second")

    def test_invalid_key_is_not_retried_and_not_tried_on_other_models(self):
        for response in (_error(400, INVALID_KEY_BODY), _error(401, "UNAUTHENTICATED"), _error(403, "PERMISSION_DENIED")):
            with self.subTest(status=response.status_code):
                result, post = self._run([response, _ok(), _ok()])
                self.assertIsInstance(result, gemini.GeminiCredentialError)
                self.assertEqual(post.call_count, 1)
                self.assertIn("GEMINI_API_KEY", str(result))
                self.assertNotIn("secret-test-key", str(result))
                self.assertFalse(self.out.exists(), "no draft may be written after a credential failure")
        self.sleep.assert_not_called()
        self.assertEqual(gemini.last_usage("news_script")["outcome"], gemini.AUTH)

    def test_all_models_unavailable_fails_after_a_bounded_number_of_requests(self):
        result, post = self._run([_error(503, "overloaded")] * 30)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(post.call_count, 9, "3 models x 3 attempts, then stop")
        self.assertIn("gemini-primary, gemini-second, gemini-third", str(result))
        self.assertFalse(self.out.exists())
        self.assertEqual(gemini.last_usage("news_script")["outcome"], "unavailable")

    def test_non_json_http_body_is_treated_as_an_unusable_draft_not_a_crash(self):
        html = Mock(status_code=200, headers={}, text="<html>502</html>")
        html.raise_for_status.return_value = None
        html.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        script, post = self._run([html, _ok()])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(script["generation"]["model"], "gemini-primary")

    def test_malformed_model_output_is_never_saved(self):
        for raw in ('{"title": "truncated', "not json at all", '"just a string"', "{}"):
            with self.subTest(raw=raw):
                result, post = self._run([_ok(raw=raw)] * 3)
                self.assertIsInstance(result, ValueError)
                self.assertEqual(post.call_count, 3, "initial draft plus two corrections, then stop")
                self.assertFalse(self.out.exists())

    def test_empty_candidate_list_is_handled(self):
        empty = Mock(status_code=200, headers={}, text="")
        empty.raise_for_status.return_value = None
        empty.json.return_value = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        result, _ = self._run([empty] * 3)
        self.assertIsInstance(result, ValueError)
        self.assertFalse(self.out.exists())

    def test_invalid_model_configuration_fails_before_any_request(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "models/has space"}):
            result, post = self._run([_ok()])
        self.assertIsInstance(result, gemini.GeminiConfigError)
        post.assert_not_called()

    def test_empty_model_variable_uses_the_default_instead_of_an_empty_url(self):
        # An unset Actions variable reaches the process as an empty string.
        with patch.dict("os.environ", {"GEMINI_MODEL": ""}):
            script, post = self._run([_ok()])
        self.assertEqual(self._models(post), [gemini.DEFAULT_MODEL])
        self.assertEqual(script["generation"]["configured_model"], gemini.DEFAULT_MODEL)


class GeminiRetryJitterTests(unittest.TestCase):
    def test_backoff_is_jittered_so_parallel_runs_do_not_retry_in_lockstep(self):
        seen = set()
        for _ in range(40):
            first = Mock(status_code=503, headers={}, text="busy")
            second = Mock(status_code=200)
            second.raise_for_status.return_value = None
            with patch.object(writer.requests, "post", side_effect=[first, second]), \
                 patch.object(writer.time, "sleep") as sleep:
                writer._post_gemini_with_retries("https://example.test", "key", {})
            seen.add(sleep.call_args.args[0])
        self.assertGreater(len(seen), 10)
        self.assertTrue(all(10 <= value <= 11 for value in seen))

    def test_retry_after_is_never_exceeded_beyond_the_cap(self):
        first = Mock(status_code=429, headers={"Retry-After": "3600"}, text="quota")
        second = Mock(status_code=200)
        second.raise_for_status.return_value = None
        with patch.object(writer.requests, "post", side_effect=[first, second]), \
             patch.object(writer.time, "sleep") as sleep:
            writer._post_gemini_with_retries("https://example.test", "key", {})
        self.assertLessEqual(sleep.call_args.args[0], 60)


class DraftCommandReportingTests(unittest.TestCase):
    """The drafting command must say *why* it failed (issue #46 runs gave no reason)."""

    def setUp(self):
        from scripts import draft_reviewed_news_short as command
        self.command = command
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = self.root / "packet.json"
        self.packet.write_text(json.dumps(PACKET), encoding="utf-8")
        self.out = self.root / "draft.json"
        self.summary = self.root / "summary.md"
        patches = [
            patch.dict("os.environ", {"GEMINI_API_KEY": "secret-test-key", "GEMINI_MODEL": "gemini-primary",
                                      "GEMINI_FALLBACK_MODELS": "gemini-second",
                                      "GITHUB_STEP_SUMMARY": str(self.summary)}),
            patch.object(writer, "verify_packet", return_value={"safe_to_draft": True, "fact_checks": []}),
            patch.object(writer.time, "sleep"),
            patch.object(gemini, "_log_path", return_value=None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _main(self, responses):
        with patch.object(writer.requests, "post", side_effect=responses), \
             patch("sys.stdout"), patch("sys.stderr"):
            return self.command.main([str(self.packet), "--output", str(self.out)])

    def _failure(self):
        return json.loads((self.root / "draft_failure.json").read_text(encoding="utf-8"))

    def test_success_returns_zero_and_summarises_the_model(self):
        self.assertEqual(self._main([_ok()]), 0)
        self.assertTrue(self.out.exists())
        self.assertFalse((self.root / "draft_failure.json").exists())
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("READY FOR HUMAN REVIEW", summary)
        self.assertIn("gemini-primary", summary)
        self.assertIn("nothing was rendered or uploaded", summary)

    def test_rejected_key_is_reported_as_credentials(self):
        self.assertEqual(self._main([_error(400, INVALID_KEY_BODY)]), self.command.EXIT_CREDENTIALS)
        report = self._failure()
        self.assertEqual(report["category"], "credentials")
        self.assertIn("GEMINI_API_KEY", report["advice"])
        text = (self.root / "draft_failure.json").read_text(encoding="utf-8") + self.summary.read_text(encoding="utf-8")
        self.assertNotIn("secret-test-key", text)

    def test_missing_key_is_reported_as_credentials(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            self.assertEqual(self._main([]), self.command.EXIT_CREDENTIALS)
        self.assertEqual(self._failure()["category"], "credentials")

    def test_outage_on_every_model_is_reported_as_provider_unavailable(self):
        self.assertEqual(self._main([_error(503, "overloaded")] * 12), self.command.EXIT_PROVIDER)
        report = self._failure()
        self.assertEqual(report["category"], "provider_unavailable")
        self.assertEqual(report["attempted_models"], ["gemini-primary", "gemini-second"])
        self.assertTrue(report["model_failures"])

    def test_drafts_that_break_the_rules_are_reported_as_validation_failed(self):
        short = _draft(_scenes(count=5, words=4))
        self.assertEqual(self._main([_ok(short)] * 3), self.command.EXIT_VALIDATION)
        report = self._failure()
        self.assertEqual(report["category"], "validation_failed")
        self.assertIn("word count", report["reason"])
        self.assertFalse(self.out.exists())

    def test_unapproved_packet_is_reported_as_packet_rejected(self):
        self.packet.write_text(json.dumps(dict(PACKET, review_status="pending")), encoding="utf-8")
        self.assertEqual(self._main([]), self.command.EXIT_PACKET)
        self.assertEqual(self._failure()["category"], "packet_rejected")

    def test_source_gate_rejection_is_reported_as_packet_rejected(self):
        blocked = {"safe_to_draft": False,
                   "fact_checks": [{"ok": False, "fact_index": 1, "reason": "single unverified origin"}]}
        with patch.object(writer, "verify_packet", return_value=blocked):
            self.assertEqual(self._main([]), self.command.EXIT_PACKET)
        report = self._failure()
        self.assertEqual(report["category"], "packet_rejected")
        self.assertIn("single unverified origin", report["reason"])
        self.assertIn("must not be bypassed", report["advice"])

    def test_bad_model_configuration_is_reported_as_configuration(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "not a model"}):
            self.assertEqual(self._main([]), self.command.EXIT_CONFIG)
        self.assertEqual(self._failure()["category"], "configuration")

    def test_a_stale_failure_report_is_removed_by_a_successful_run(self):
        (self.root / "draft_failure.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._main([_ok()]), 0)
        self.assertFalse((self.root / "draft_failure.json").exists())


if __name__ == "__main__":
    unittest.main()
