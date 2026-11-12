"""Shared Gemini policy and the script-engine model fallback (all HTTP mocked)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core import gemini, script_engine


def _ok(text='{"title":"ok"}'):
    response = Mock(status_code=200, text="", headers={})
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    return response


def _error(status, body="", headers=None):
    return Mock(status_code=status, text=body, headers=headers or {})


INVALID_KEY_BODY = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                         "message": "API key not valid. Please pass a valid API key.",
                                         "details": [{"reason": "API_KEY_INVALID"}]}})


class ModelChainTests(unittest.TestCase):
    def test_primary_first_then_fallbacks_without_duplicates(self):
        self.assertEqual(
            gemini.model_chain("gemini-3.8-flash", ["gemini-3.7-flash", "gemini-3.8-flash", " gemini-3.6-flash "]),
            ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"])

    def test_comma_separated_string_and_models_prefix_are_accepted(self):
        self.assertEqual(
            gemini.model_chain("models/gemini-3.8-flash", "gemini-3.7-flash, ,gemini-3.6-flash"),
            ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"])

    def test_environment_is_the_source_of_truth(self):
        env = {"GEMINI_MODEL": "gemini-x-pro", "GEMINI_FALLBACK_MODELS": "gemini-y, gemini-z"}
        self.assertEqual(gemini.model_chain(environ=env), ["gemini-x-pro", "gemini-y", "gemini-z"])

    def test_legacy_single_fallback_variable_still_works(self):
        self.assertEqual(gemini.configured_fallbacks({"GEMINI_FALLBACK_MODEL": "gemini-old"}), ["gemini-old"])

    def test_empty_variable_keeps_the_default_fallbacks(self):
        """An unset Actions variable arrives as "" and must not remove the safety net."""
        for blank in ("", "   "):
            self.assertEqual(
                gemini.model_chain(environ={"GEMINI_MODEL": "gemini-a", "GEMINI_FALLBACK_MODELS": blank}),
                ["gemini-a", *gemini.DEFAULT_FALLBACK_MODELS])
        self.assertEqual(gemini.model_chain(environ={"GEMINI_MODEL": "", "GEMINI_FALLBACK_MODELS": ""}),
                         [gemini.DEFAULT_MODEL, *gemini.DEFAULT_FALLBACK_MODELS])

    def test_fallbacks_can_be_disabled_explicitly(self):
        self.assertEqual(gemini.model_chain(environ={"GEMINI_MODEL": "gemini-a", "GEMINI_FALLBACK_MODELS": "none"}),
                         ["gemini-a"])

    def test_unset_configuration_uses_the_single_default_chain(self):
        self.assertEqual(gemini.model_chain(environ={}),
                         [gemini.DEFAULT_MODEL, *gemini.DEFAULT_FALLBACK_MODELS])

    def test_invalid_ids_are_rejected_with_the_setting_to_fix(self):
        for bad in ("gemini 3 flash", "gemini/../../v1", "gemini?key=abc", "x", "Gemini-Flash", "a" * 90):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(gemini.GeminiConfigError, "GEMINI_MODEL"):
                    gemini.model_chain(bad, [])
        with self.assertRaises(gemini.GeminiConfigError):
            gemini.model_chain("gemini-ok", ["bad id"])

    def test_non_strict_mode_drops_invalid_ids_and_reports_them(self):
        self.assertEqual(gemini.model_chain("gemini-ok", ["bad id", "gemini-b"], strict=False),
                         ["gemini-ok", "gemini-b"])
        self.assertEqual(gemini.invalid_model_ids("gemini-ok", ["bad id", "gemini-b"]), ["bad id"])

    def test_no_model_at_all_is_a_configuration_error(self):
        with self.assertRaises(gemini.GeminiConfigError):
            gemini.model_chain("", [])

    def test_default_model_ids_are_written_in_one_module_only(self):
        """Regression: two modules carried different hard-coded fallback lists."""
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for path in sorted((root / "core").glob("*.py")) + [root / "run.py", root / "run_daily.py"]:
            if path.name == "gemini.py":
                continue
            text = path.read_text(encoding="utf-8")
            for model in (gemini.DEFAULT_MODEL, *gemini.DEFAULT_FALLBACK_MODELS):
                if f'"{model}' in text or f"'{model}" in text or f",{model}" in text:
                    offenders.append(f"{path.name}: {model}")
        self.assertEqual(offenders, [])

    def test_config_module_uses_the_shared_chain(self):
        from core import config
        if "GEMINI_FALLBACK_MODELS" not in os.environ and "GEMINI_FALLBACK_MODEL" not in os.environ:
            self.assertEqual(config.GEMINI_FALLBACK_MODELS, list(gemini.DEFAULT_FALLBACK_MODELS))
        self.assertTrue(config.GEMINI_MODEL)


class ClassificationTests(unittest.TestCase):
    def test_each_status_maps_to_its_own_kind(self):
        cases = {429: gemini.RATE_LIMITED, 500: gemini.SERVER, 502: gemini.SERVER, 503: gemini.SERVER,
                 504: gemini.SERVER, 408: gemini.TRANSIENT, 404: gemini.MODEL_NOT_FOUND,
                 401: gemini.AUTH, 403: gemini.AUTH, 400: gemini.BAD_REQUEST, 422: gemini.BAD_REQUEST}
        for status, kind in cases.items():
            with self.subTest(status=status):
                self.assertEqual(gemini.classify(status, "x"), kind)

    def test_google_reports_an_invalid_key_as_http_400(self):
        self.assertEqual(gemini.classify(400, INVALID_KEY_BODY), gemini.AUTH)
        self.assertEqual(gemini.classify(400, '{"error":{"message":"Invalid JSON payload"}}'), gemini.BAD_REQUEST)

    def test_timeouts_and_connection_errors_are_network_failures(self):
        self.assertEqual(gemini.classify(None, network_error=True), gemini.NETWORK)

    def test_only_transient_kinds_are_retryable(self):
        retryable = {k for k in (gemini.AUTH, gemini.MODEL_NOT_FOUND, gemini.RATE_LIMITED, gemini.SERVER,
                                 gemini.TRANSIENT, gemini.BAD_REQUEST, gemini.NETWORK, gemini.MALFORMED)
                     if gemini.is_retryable(k)}
        self.assertEqual(retryable, {gemini.RATE_LIMITED, gemini.SERVER, gemini.TRANSIENT, gemini.NETWORK})

    def test_auth_message_names_the_secret_and_never_a_value(self):
        message = gemini.describe(gemini.AUTH)
        self.assertIn("GEMINI_API_KEY", message)
        self.assertNotIn("AIza", message)


class BackoffTests(unittest.TestCase):
    def test_jitter_is_added_and_bounded(self):
        delays = {gemini.backoff_delay(0, schedule=(10, 30, 60), cap=60, jitter=1.0) for _ in range(200)}
        self.assertGreater(len(delays), 50, "delays must vary between calls")
        self.assertTrue(all(10.0 <= d <= 11.0 for d in delays), sorted(delays)[:3])

    def test_schedule_grows_and_never_exceeds_the_cap(self):
        with patch.object(gemini.random, "uniform", return_value=0.0):
            self.assertEqual([gemini.backoff_delay(i, schedule=(10, 30, 60), cap=60) for i in range(5)],
                             [10, 30, 60, 60, 60])
            self.assertEqual([gemini.backoff_delay(i, base=5, cap=30) for i in range(4)], [5, 10, 20, 30])
        self.assertLessEqual(max(gemini.backoff_delay(9, base=5, cap=30, jitter=5) for _ in range(100)), 30)

    def test_retry_after_seconds_wins_and_is_clamped(self):
        with patch.object(gemini.random, "uniform", return_value=0.0):
            self.assertEqual(gemini.backoff_delay(0, retry_after="17", cap=60), 17)
            self.assertEqual(gemini.backoff_delay(0, retry_after="999", cap=60), 60)
            self.assertEqual(gemini.backoff_delay(0, retry_after="0", cap=60), 1)
            self.assertEqual(gemini.backoff_delay(0, retry_after="-5", cap=60), 1)

    def test_retry_after_http_date_is_understood(self):
        now = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
        header = format_datetime(now + timedelta(seconds=25), usegmt=True)
        self.assertAlmostEqual(gemini.parse_retry_after(header, now=now), 25, places=3)
        self.assertEqual(gemini.parse_retry_after(format_datetime(now - timedelta(hours=1), usegmt=True), now=now), 0)

    def test_garbage_retry_after_falls_back_to_the_schedule(self):
        self.assertIsNone(gemini.parse_retry_after("soon"))
        self.assertIsNone(gemini.parse_retry_after(""))
        with patch.object(gemini.random, "uniform", return_value=0.0):
            self.assertEqual(gemini.backoff_delay(1, retry_after="soon", schedule=(10, 30, 60), cap=60), 30)


class ResponseShapeTests(unittest.TestCase):
    def test_text_is_joined_and_missing_content_is_empty(self):
        data = {"candidates": [{"content": {"parts": [{"text": "a"}, {"inlineData": {}}, {"text": "b"}]}}]}
        self.assertEqual(gemini.response_text(data), "ab")
        for broken in ({}, {"candidates": []}, {"candidates": [{}]}, {"candidates": [{"content": {"parts": "x"}}]},
                       None, [], "text"):
            with self.subTest(broken=broken):
                self.assertEqual(gemini.response_text(broken), "")

    def test_finish_reason(self):
        self.assertEqual(gemini.finish_reason({"candidates": [{"finishReason": "max_tokens"}]}), "MAX_TOKENS")
        self.assertEqual(gemini.finish_reason({}), "")


class UsageRecordTests(unittest.TestCase):
    def test_record_is_remembered_and_appended_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AI_STUDIO_LOG_DIR": tmp}):
            log = Path(tmp) / "llm_usage.jsonl"
            with patch.object(gemini, "_log_path", return_value=log):
                gemini.record_usage("unit-test", model="gemini-b", outcome="ok",
                                    attempted=["gemini-a", "gemini-b"], failures=["gemini-a: HTTP 503"], seconds=1.234)
            entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(entry["model"], "gemini-b")
        self.assertEqual(entry["attempted_models"], ["gemini-a", "gemini-b"])
        self.assertEqual(entry["outcome"], "ok")
        self.assertEqual(set(entry), {"at", "purpose", "provider", "model", "outcome",
                                      "attempted_models", "failures", "seconds"})
        self.assertEqual(gemini.last_usage("unit-test")["model"], "gemini-b")

    def test_an_unwritable_log_never_breaks_the_pipeline(self):
        with patch.object(gemini, "_log_path", return_value=Path("/proc/definitely/not/writable/x.jsonl")):
            entry = gemini.record_usage("unit-test-2", model=None, outcome="auth")
        self.assertEqual(entry["outcome"], "auth")


class ScriptEngineFallbackTests(unittest.TestCase):
    """End-to-end model fallback through script_engine with the HTTP layer mocked."""

    def setUp(self):
        patches = [
            patch.object(script_engine, "GEMINI_API_KEY", "test-key"),
            patch.object(script_engine, "GEMINI_MODEL", "gemini-primary"),
            patch.object(script_engine, "GEMINI_FALLBACK_MODELS", ["gemini-second", "gemini-third"]),
            patch.object(script_engine.time, "sleep"),
            patch.object(gemini, "_log_path", return_value=None),
            patch.dict("os.environ", {"GEMINI_AUTO_DISCOVER_MODELS": "0"}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.sleep = script_engine.time.sleep

    def _models_called(self, post):
        return [call.args[0].split("/models/")[1].split(":")[0] for call in post.call_args_list]

    def test_rate_limited_primary_falls_back_and_records_the_model_used(self):
        responses = [_error(429, "quota"), _error(429, "quota"), _ok('{"title":"from second"}')]
        with patch.object(script_engine.requests, "post", side_effect=responses) as post:
            text = script_engine._ask_gemini_models("prompt")
        self.assertEqual(text, '{"title":"from second"}')
        self.assertEqual(self._models_called(post), ["gemini-primary", "gemini-primary", "gemini-second"])
        usage = gemini.last_usage("script")
        self.assertEqual(usage["model"], "gemini-second")
        self.assertEqual(usage["outcome"], "ok")
        self.assertEqual(usage["attempted_models"], ["gemini-primary", "gemini-second"])
        self.assertTrue(any("gemini-primary" in item for item in usage["failures"]))

    def test_each_server_error_code_triggers_fallback(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                responses = [_error(status, "down"), _error(status, "down"), _ok()]
                with patch.object(script_engine.requests, "post", side_effect=responses) as post:
                    self.assertEqual(script_engine._ask_gemini_models("prompt"), '{"title":"ok"}')
                self.assertEqual(self._models_called(post)[-1], "gemini-second")

    def test_retry_after_is_honoured_and_capped(self):
        responses = [_error(429, "slow down", {"Retry-After": "7"}), _ok()]
        with patch.object(script_engine.requests, "post", side_effect=responses), \
             patch.object(gemini.random, "uniform", return_value=0.0):
            script_engine._ask_model("prompt", "gemini-primary", attempts=2)
        self.sleep.assert_called_once_with(7)
        self.sleep.reset_mock()
        responses = [_error(503, "busy", {"Retry-After": "86400"}), _ok()]
        with patch.object(script_engine.requests, "post", side_effect=responses), \
             patch.object(gemini.random, "uniform", return_value=0.0):
            script_engine._ask_model("prompt", "gemini-primary", attempts=2)
        self.sleep.assert_called_once_with(90)

    def test_timeout_is_retried_then_falls_back(self):
        responses = [requests.Timeout("read timed out"), requests.ConnectionError("reset"), _ok()]
        with patch.object(script_engine.requests, "post", side_effect=responses) as post:
            self.assertEqual(script_engine._ask_gemini_models("prompt"), '{"title":"ok"}')
        self.assertEqual(self._models_called(post), ["gemini-primary", "gemini-primary", "gemini-second"])

    def test_invalid_key_stops_immediately_without_trying_other_models(self):
        with patch.object(script_engine.requests, "post", return_value=_error(400, INVALID_KEY_BODY)) as post:
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY") as caught:
                script_engine._ask_gemini_models("prompt")
        self.assertEqual(post.call_count, 1, "an invalid key must not be retried or tried on other models")
        self.sleep.assert_not_called()
        self.assertNotIn("test-key", str(caught.exception))
        self.assertEqual(gemini.last_usage("script")["outcome"], gemini.AUTH)

    def test_http_403_is_also_a_credential_failure(self):
        with patch.object(script_engine.requests, "post", return_value=_error(403, "PERMISSION_DENIED")) as post:
            with self.assertRaises(script_engine.GeminiRequestError) as caught:
                script_engine._ask_model("prompt", "gemini-primary", attempts=3)
        self.assertEqual(caught.exception.kind, gemini.AUTH)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(post.call_count, 1)

    def test_retired_model_is_skipped_without_waiting(self):
        responses = [_error(404, "model not found"), _ok('{"title":"second"}')]
        with patch.object(script_engine.requests, "post", side_effect=responses) as post:
            self.assertEqual(script_engine._ask_gemini_models("prompt"), '{"title":"second"}')
        self.assertEqual(self._models_called(post), ["gemini-primary", "gemini-second"])
        self.sleep.assert_not_called()

    def test_malformed_body_is_a_typed_failure_that_falls_back(self):
        broken = Mock(status_code=200, text="<html>bad gateway</html>", headers={})
        broken.json.side_effect = ValueError("Expecting value")
        with patch.object(script_engine.requests, "post", return_value=broken):
            with self.assertRaises(script_engine.GeminiRequestError) as caught:
                script_engine._ask_model("prompt", "gemini-primary", attempts=1)
        self.assertEqual(caught.exception.kind, gemini.MALFORMED)
        # An unparseable body is not re-requested from the same model: move on.
        with patch.object(script_engine.requests, "post", side_effect=[broken, _ok()]) as post:
            self.assertEqual(script_engine._ask_gemini_models("prompt"), '{"title":"ok"}')
        self.assertEqual(self._models_called(post), ["gemini-primary", "gemini-second"])

    def test_all_models_down_fails_after_a_bounded_number_of_requests(self):
        with patch.object(script_engine.requests, "post", return_value=_error(503, "overloaded")) as post:
            with self.assertRaisesRegex(RuntimeError, "all configured models"):
                script_engine._ask_gemini_models("prompt")
        self.assertEqual(post.call_count, 6, "3 models x 2 attempts, then stop")
        usage = gemini.last_usage("script")
        self.assertEqual(usage["outcome"], "unavailable")
        self.assertEqual(usage["attempted_models"], ["gemini-primary", "gemini-second", "gemini-third"])

    def test_invalid_model_configuration_is_reported_before_any_request(self):
        with patch.object(script_engine, "GEMINI_MODEL", "not a model"), \
             patch.object(script_engine.requests, "post") as post:
            with self.assertRaisesRegex(RuntimeError, "Invalid Gemini model id"):
                script_engine._ask_gemini_models("prompt")
        post.assert_not_called()

    def test_generated_script_is_stamped_with_the_model_that_wrote_it(self):
        gemini.record_usage("script", model="gemini-second", outcome="ok",
                            attempted=["gemini-primary", "gemini-second"])
        script = script_engine._stamp_generation({"title": "t"})
        self.assertEqual(script["generation"], {
            "provider": "gemini", "model": "gemini-second",
            "attempted_models": ["gemini-primary", "gemini-second"], "fallback_used": True})

    def test_safe_json_repairs_only(self):
        self.assertEqual(script_engine._parse('```json\n{"a": [1, 2,], }\n```'), {"a": [1, 2]})
        for unsafe in ('{"title": "cut off', '{"title": unquoted}', "no json here", ""):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    script_engine._parse(unsafe)


class VisualReviewFallbackTests(unittest.TestCase):
    """The advisory visual check shares the same model and credential rules."""

    def setUp(self):
        from PIL import Image
        from core import visual_semantics
        self.module = visual_semantics
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        frame = Path(self.tmp.name) / "frame.jpg"
        Image.new("RGB", (96, 64), (20, 80, 120)).save(frame)
        self.shot = {"narration": "A cargo ship approaches the port.",
                     "visual_prompt": "Wide shot of a cargo ship approaching a port.",
                     "visual_type": "stock", "sample_frames": [str(frame)]}
        for item in (patch.object(visual_semantics, "GEMINI_FALLBACK_MODELS", ["vision-second", "bad id"]),
                     patch.object(visual_semantics.time, "sleep")):
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def _verdict():
        body = {"verdict": "match", "match_score": 0.9, "visible_evidence": "ship", "concerns": [],
                "recommendation": "keep"}
        return _ok(json.dumps(body))

    def test_retired_primary_model_falls_through_to_the_next_model(self):
        with patch.object(self.module.requests, "post", side_effect=[_error(404, "not found"), self._verdict()]) as post:
            result = self.module.verify_shot_semantics(self.shot, api_key="k", model="vision-primary")
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["model"], "vision-second")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(post.call_count, 2)

    def test_invalid_key_is_reported_once_and_other_models_are_not_tried(self):
        with patch.object(self.module.requests, "post", return_value=_error(400, INVALID_KEY_BODY)) as post:
            result = self.module.verify_shot_semantics(self.shot, api_key="secret-k", model="vision-primary")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["failure_kind"], gemini.AUTH)
        self.assertIn("GEMINI_API_KEY", result["reason"])
        self.assertNotIn("secret-k", json.dumps(result))
        self.assertTrue(result["human_review_required"])

    def test_invalid_configured_id_never_reaches_a_request_url(self):
        with patch.object(self.module.requests, "post", return_value=_error(503, "busy")) as post:
            result = self.module.verify_shot_semantics(self.shot, api_key="k", model="vision-primary")
        urls = [call.args[0] for call in post.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertFalse(any("bad id" in url for url in urls))
        self.assertEqual(result["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
