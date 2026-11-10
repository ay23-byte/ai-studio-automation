"""Regression tests for the strict zero-paid provider policy."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.images import _cloudflare, generate_ai_video
from core.muapi_video import generate_muapi_video


class ZeroPaidProviderPolicyTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "1",
        "CLOUDFLARE_ACCOUNT_ID": "account",
        "CLOUDFLARE_API_TOKEN": "token",
    }, clear=True)
    @patch("core.images.requests.post")
    def test_cloudflare_inference_is_blocked(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(_cloudflare("cinematic scene", Path(directory) / "image.png"))

    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "1",
        "MUAPI_VIDEO_ENABLED": "1",
        "MUAPI_API_KEY": "test-key",
        "MUAPI_VIDEO_ENDPOINT": "endpoint",
        "AI_VIDEO_ENABLED": "1",
        "REPLICATE_API_TOKEN": "test-token",
        "REPLICATE_VIDEO_MODEL": "owner/model",
    }, clear=True)
    @patch("core.images.requests.post")
    def test_hosted_video_providers_are_blocked(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(generate_ai_video("cinematic scene", Path(directory) / "clip.mp4"))
        post.assert_not_called()

    @patch.dict(os.environ, {
        "MUAPI_VIDEO_ENABLED": "1",
        "MUAPI_API_KEY": "test-key",
        "MUAPI_VIDEO_ENDPOINT": "endpoint",
    }, clear=True)
    def test_muapi_direct_entry_is_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(generate_muapi_video("cinematic scene", Path(directory) / "clip.mp4"))


if __name__ == "__main__":
    unittest.main()
