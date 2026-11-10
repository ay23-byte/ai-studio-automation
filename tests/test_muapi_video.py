"""Hosted AI video providers are hard-disabled; tests make no real API calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.muapi_video import generate_muapi_video


class MuAPIVideoPolicyTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "0",
        "MUAPI_VIDEO_ENABLED": "1",
        "MUAPI_API_KEY": "test-key",
        "MUAPI_VIDEO_ENDPOINT": "test-endpoint",
    }, clear=True)
    @patch("core.muapi_video.requests.post")
    def test_muapi_remains_disabled_even_if_credentials_and_flags_exist(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(generate_muapi_video("test prompt", Path(directory) / "clip.mp4"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
