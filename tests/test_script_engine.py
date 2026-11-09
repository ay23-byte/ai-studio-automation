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
            minimum_words=812,
            target_words=1050,
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


class LongformStoryboardRepairTests(unittest.TestCase):
    def test_missing_visual_beats_are_repaired_without_inventing_evidence(self):
        script = {
            "sections": [{
                "title": "Outro",
                "narration": (
                    "The villages are not simply disappearing. Families are making "
                    "difficult choices as work, schools and services change. "
                    "The future depends on whether opportunity can return."
                ),
                "visuals": [],
            }]
        }
        repaired = script_engine._repair_longform_visual_anchors(script)
        validated = script_engine.validate_longform_storyboard(repaired)
        visuals = validated["sections"][0]["visuals"]
        self.assertEqual(len(visuals), 3)
        self.assertTrue(all(v["visual_type"] == "presenter" for v in visuals))
        narration = validated["sections"][0]["narration"].casefold()
        cursor = 0
        for beat in visuals:
            anchor = beat["narration_anchor"].casefold()
            position = narration.find(anchor, cursor)
            self.assertGreaterEqual(position, 0)
            cursor = position + len(anchor)
            self.assertEqual(beat["source_refs"], [])
            self.assertIn("unverified", beat["visual_prompt"].lower())

    def test_short_narration_cannot_be_fabricated_into_three_beats(self):
        script = {"sections": [{"narration": "Too short.", "visuals": []}]}
        with self.assertRaisesRegex(ValueError, "too little narration"):
            script_engine._repair_longform_visual_anchors(script)



class LongformNarrationExpansionTests(unittest.TestCase):
    def test_chapter_expansion_adds_narration_and_clears_runtime_floor(self):
        script = {
            "sections": [{
                "title": "Apollo context",
                "section_type": "context",
                "narration": "This is the existing narration with enough words to establish the section context.",
                "visuals": [],
            }]
        }
        addition = (
            "The important point is not simply that the engineering was difficult. "
            "The choices described above show how tightly connected the mission systems were. "
            "A decision in one area shaped the demands placed on the others, which is why "
            "the story is best understood as one coordinated technical effort rather than "
            "a collection of isolated achievements."
        )
        response = __import__("json").dumps({
            "section_additions": [{"section_index": 0, "additional_narration": addition}]
        })
        with patch.object(script_engine, "_ask_gemini", return_value=response), \
             patch.object(script_engine, "_repair_longform_visual_anchors", side_effect=lambda value: value), \
             patch.object(script_engine, "validate_longform_storyboard", side_effect=lambda value: value):
            expanded = script_engine._expand_longform_narration(
                script, "Apollo 11", "science", "English",
                target_words=45, minimum_words=35, max_passes=1,
            )
        self.assertGreaterEqual(script_engine.count_longform_narration_words(expanded), 35)
        self.assertIn("coordinated technical effort", expanded["sections"][0]["narration"])
        self.assertNotIn("coordinated technical effort", script["sections"][0]["narration"])

    def test_chapter_expansion_still_fails_if_model_does_not_add_enough(self):
        script = {
            "sections": [{
                "title": "Context",
                "section_type": "context",
                "narration": "A short but valid original narration for this chapter.",
                "visuals": [],
            }]
        }
        response = __import__("json").dumps({
            "section_additions": [{
                "section_index": 0,
                "additional_narration": "This is a small supported addition that does not make the documentary long enough."
            }]
        })
        with patch.object(script_engine, "_ask_gemini", return_value=response), \
             patch.object(script_engine, "_repair_longform_visual_anchors", side_effect=lambda value: value), \
             patch.object(script_engine, "validate_longform_storyboard", side_effect=lambda value: value):
            with self.assertRaisesRegex(ValueError, "remains too short after chapter expansion"):
                script_engine._expand_longform_narration(
                    script, "Apollo 11", "science", "English",
                    target_words=100, minimum_words=90, max_passes=2,
                )


if __name__ == "__main__":
    unittest.main()
