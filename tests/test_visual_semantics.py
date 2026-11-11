import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.visual_semantics import verify_shot_semantics


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class VisualSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.frame = Path(self.temp.name) / "frame.jpg"
        Image.new("RGB", (96, 64), (20, 80, 120)).save(self.frame)
        self.shot = {
            "narration": "A cargo ship approaches the port.",
            "visual_prompt": "Wide documentary shot of a cargo ship approaching a port.",
            "visual_type": "stock",
            "sample_frames": [str(self.frame)],
        }

    def test_missing_api_key_is_safe_and_explicit(self):
        result = verify_shot_semantics(self.shot, api_key="")
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["human_review_required"])

    def test_missing_frames_are_unavailable(self):
        result = verify_shot_semantics(dict(self.shot, sample_frames=[]), api_key="test")
        self.assertEqual(result["status"], "unavailable")

    @patch("core.visual_semantics.requests.post")
    def test_match_response_is_parsed_and_keeps_human_review_required(self, post):
        body = {"verdict": "match", "match_score": 0.91,
                "visible_evidence": "A large ship is visible near a port.",
                "concerns": [], "recommendation": "Keep for editor review."}
        post.return_value = _Response({
            "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]
        })
        result = verify_shot_semantics(self.shot, api_key="test-key", model="vision-test")
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["match_score"], 0.91)
        self.assertTrue(result["human_review_required"])
        payload = post.call_args.kwargs["json"]
        self.assertTrue(any("inlineData" in part for part in payload["contents"][0]["parts"]))

    @patch("core.visual_semantics.requests.post")
    def test_low_confidence_match_is_downgraded_to_uncertain(self, post):
        body = {"verdict": "match", "match_score": 0.4}
        post.return_value = _Response({
            "candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]
        })
        result = verify_shot_semantics(self.shot, api_key="test-key")
        self.assertEqual(result["status"], "uncertain")

    @patch("core.visual_semantics.time.sleep")
    @patch("core.visual_semantics.GEMINI_FALLBACK_MODELS", ["vision-fallback"])
    @patch("core.visual_semantics.requests.post")
    def test_rate_limited_primary_uses_configured_fallback(self, post, sleep):
        class RateLimited:
            status_code = 429
            headers = {"Retry-After": "1"}

        body = {"verdict": "match", "match_score": 0.9,
                "visible_evidence": "A cargo ship is visible."}
        post.side_effect = [
            RateLimited(),
            _Response({"candidates": [{"content": {"parts": [{"text": json.dumps(body)}]}}]}),
        ]
        result = verify_shot_semantics(self.shot, api_key="test-key", model="vision-primary")
        self.assertEqual(result["status"], "match")
        self.assertEqual(result["model"], "vision-fallback")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()

    @patch("core.visual_semantics.requests.post")
    def test_api_failure_is_returned_as_error_not_a_match(self, post):
        post.side_effect = TimeoutError("simulated timeout")
        result = verify_shot_semantics(self.shot, api_key="test-key")
        self.assertEqual(result["status"], "error")
        self.assertIn("timeout", result["reason"].lower())


if __name__ == "__main__":
    unittest.main()
