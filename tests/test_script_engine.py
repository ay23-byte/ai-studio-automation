import unittest
from unittest.mock import patch, Mock

from core import script_engine


class GeminiFailoverTests(unittest.TestCase):
    def test_primary_outage_uses_next_configured_model(self):
        outage = script_engine.GeminiRequestError(
            "primary", "HTTP 503 unavailable", status_code=503, retryable=True
        )
        with patch.object(script_engine, "GEMINI_API_KEY", "test-key"), \
             patch.object(script_engine, "GEMINI_MODEL", "primary"), \
             patch.object(script_engine, "GEMINI_FALLBACK_MODELS", ["fallback-a"]), \
             patch.object(script_engine.time, "sleep"), \
             patch.object(script_engine, "_ask_model", side_effect=[outage, "valid response"]) as call:
            result = script_engine._ask_gemini("prompt")
        self.assertEqual(result, "valid response")
        self.assertEqual([c.args[1] for c in call.call_args_list], ["primary", "fallback-a"])

    def test_non_retryable_auth_error_does_not_rotate_models(self):
        failure = script_engine.GeminiRequestError(
            "primary", "HTTP 403 forbidden", status_code=403, retryable=False
        )
        with patch.object(script_engine, "GEMINI_API_KEY", "test-key"), \
             patch.object(script_engine, "GEMINI_MODEL", "primary"), \
             patch.object(script_engine, "GEMINI_FALLBACK_MODELS", ["fallback"]), \
             patch.object(script_engine, "_ask_model", side_effect=failure) as call:
            with self.assertRaisesRegex(RuntimeError, "403"):
                script_engine._ask_gemini("prompt")
        call.assert_called_once()

    def test_missing_api_key_fails_before_network(self):
        with patch.object(script_engine, "GEMINI_API_KEY", ""):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                script_engine._ask_gemini("prompt")

    def test_longform_prompt_formats_without_json_key_error(self):
        prompt = script_engine.LONGFORM_PROMPT.format(
            topic="How Black Holes Bend Space and Time",
            category="science",
            duration_min=5,
            duration_max=7,
            language="Hindi",
        )
        self.assertIn('"title"', prompt)
        self.assertIn('"sections"', prompt)
        self.assertIn("How Black Holes Bend Space and Time", prompt)

    def test_parser_accepts_json_fence_and_trailing_commas(self):
        result = script_engine._parse('''```json
{"title":"Test","sections":[{"narration":"Hello","visuals":[],}],}
```''')
        self.assertEqual(result["title"], "Test")

    def test_parser_reports_malformed_or_truncated_json_clearly(self):
        with self.assertRaisesRegex(ValueError, "malformed or truncated JSON"):
            script_engine._parse('{"title":"unfinished"')

    def test_max_tokens_response_triggers_model_failover(self):
        response = Mock(status_code=200, text="", headers={})
        response.json.return_value = {
            "candidates": [{
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": "{\\\"title\\\":"}]}
            }]
        }
        with patch.object(script_engine, "GEMINI_API_KEY", "test-key"), patch.object(script_engine.requests, "post", return_value=response), patch.object(script_engine.time, "sleep"):
            with self.assertRaisesRegex(script_engine.GeminiRequestError, "output-token limit"):
                script_engine._ask_model("prompt", "model", attempts=1)
    def test_http_503_retries_then_returns_success(self):
        first = Mock(status_code=503, text='temporarily unavailable', headers={})
        second = Mock(status_code=200, text='', headers={})
        second.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"title":"ok"}'}]}}]
        }
        with patch.object(script_engine, "GEMINI_API_KEY", "test-key"), \
             patch.object(script_engine.requests, "post", side_effect=[first, second]) as post, \
             patch.object(script_engine.time, "sleep"), \
             patch.object(script_engine.random, "uniform", return_value=0):
            result = script_engine._ask_model("prompt", "model", attempts=2)
        self.assertEqual(result, '{"title":"ok"}')
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
