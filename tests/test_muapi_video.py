"""Tests for the opt-in MuAPI video adapter (no network or paid calls)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.muapi_video import generate_muapi_video


class MuAPIVideoTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_disabled_without_explicit_opt_in(self):
        self.assertFalse(generate_muapi_video("cinematic moon surface", "unused.mp4"))

    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "0",
        "MUAPI_VIDEO_ENABLED": "1",
        "MUAPI_API_KEY": "test-key",
        "MUAPI_VIDEO_ENDPOINT": "wan-video-endpoint",
        "MUAPI_VIDEO_INPUT_JSON": '{"duration":5,"aspect_ratio":"16:9"}',
        "MUAPI_MAX_COST_USD": "0.20",
    }, clear=True)
    @patch("core.muapi_video._video_is_valid", return_value=True)
    @patch("core.muapi_video.time.sleep")
    @patch("core.muapi_video.requests.get")
    @patch("core.muapi_video.requests.post")
    def test_submit_poll_download_and_provenance(self, post, get, sleep, valid):
        submitted = MagicMock()
        submitted.json.return_value = {"request_id": "job-123", "status": "processing",
                                       "cost": {"amount_usd": 0.04}}
        submitted.raise_for_status.return_value = None
        post.return_value = submitted

        completed = MagicMock()
        completed.json.return_value = {"status": "completed",
                                       "outputs": ["https://cdn.muapi.ai/sample.mp4"],
                                       "cost": {"amount_usd": 0.04}}
        completed.raise_for_status.return_value = None
        download = MagicMock()
        download.__enter__.return_value = download
        download.iter_content.return_value = [b"video-bytes"]
        download.raise_for_status.return_value = None
        get.side_effect = [completed, download]

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "shot.mp4"
            with patch("builtins.open", create=True):
                self.assertTrue(generate_muapi_video("Apollo lunar module", target))
            self.assertTrue(target.exists())
            metadata = json.loads(target.with_suffix(".mp4.source.json").read_text())
            self.assertEqual(metadata["provider"], "MuAPI")
            self.assertEqual(metadata["asset_type"], "ai_generated_video")
            self.assertTrue(metadata["ai_disclosure"])
            self.assertFalse(metadata["terms_reviewed"])
            sent = post.call_args.kwargs["json"]
            self.assertEqual(sent["prompt"], "Apollo lunar module")
            self.assertEqual(sent["duration"], 5)

    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "0", "MUAPI_VIDEO_ENABLED": "1", "MUAPI_API_KEY": "test-key",
        "MUAPI_VIDEO_ENDPOINT": "valid-endpoint", "MUAPI_MAX_COST_USD": "0.01",
    }, clear=True)
    @patch("core.muapi_video.requests.post")
    def test_rejects_quote_over_budget(self, post):
        response = MagicMock()
        response.json.return_value = {"request_id": "job", "cost": {"amount_usd": 0.50}}
        response.raise_for_status.return_value = None
        post.return_value = response
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(generate_muapi_video("test", Path(directory) / "x.mp4"))


if __name__ == "__main__":
    unittest.main()
