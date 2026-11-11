import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import images


class ReplicateVideoProviderTests(unittest.TestCase):
    def setUp(self):
        images._REPLICATE_VIDEO_CLIPS_CREATED = 0

    @patch.dict(os.environ, {}, clear=True)
    @patch("core.images.requests.post")
    def test_disabled_by_default_never_calls_hosted_api(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(images.generate_ai_video("cinematic scene", Path(directory) / "scene.mp4"))
        post.assert_not_called()

    @patch.dict(os.environ, {
        "FREE_ONLY_MODE": "1",
        "AI_VIDEO_ENABLED": "1",
        "REPLICATE_API_TOKEN": "secret",
        "REPLICATE_VIDEO_MODEL": "owner/model",
    }, clear=True)
    @patch("core.images.requests.post")
    def test_free_only_policy_blocks_paid_generation(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(images.generate_ai_video("cinematic scene", Path(directory) / "scene.mp4"))
        post.assert_not_called()

    @patch.dict(os.environ, {
        "AI_VIDEO_ENABLED": "1",
        "REPLICATE_API_TOKEN": "secret",
    }, clear=True)
    @patch("core.images.requests.post")
    def test_missing_model_never_calls_hosted_api(self, post):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(images.generate_ai_video("cinematic scene", Path(directory) / "scene.mp4"))
        post.assert_not_called()

    @patch.dict(os.environ, {
        "AI_VIDEO_ENABLED": "1",
        "FREE_ONLY_MODE": "0",
        "AI_VIDEO_MAX_CLIPS": "2",
        "REPLICATE_API_TOKEN": "test-token",
        "REPLICATE_VIDEO_MODEL": "owner/video-model",
        "REPLICATE_VIDEO_INPUT_JSON": '{"prompt":"{{prompt}}","aspect_ratio":"{{aspect_ratio}}"}',
    }, clear=True)
    @patch("core.images._write_source_metadata")
    @patch("core.images._valid_ai_video", return_value=True)
    @patch("core.images.requests.get")
    @patch("core.images.requests.post")
    def test_explicit_opt_in_generates_and_records_video(
        self, post, get, valid_video, write_metadata
    ):
        created = MagicMock()
        created.json.return_value = {
            "id": "prediction-123",
            "status": "succeeded",
            "output": "https://files.example/video.mp4",
            "urls": {"web": "https://replicate.com/p/prediction-123"},
        }
        post.return_value = created
        download = MagicMock()
        download.__enter__.return_value = download
        download.iter_content.return_value = [b"fake-video-bytes"]
        get.return_value = download

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scene.mp4"
            result = images.generate_ai_video("A cinematic moon landing", output, "16:9")
            self.assertTrue(result)
            self.assertTrue(output.is_file())
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["input"]["prompt"], "A cinematic moon landing")
            self.assertEqual(payload["input"]["aspect_ratio"], "16:9")
            self.assertEqual(post.call_args.args[0],
                             "https://api.replicate.com/v1/models/owner/video-model/predictions")
            write_metadata.assert_called_once()
            self.assertEqual(write_metadata.call_args.args[1]["asset_type"], "ai_generated_video")
        valid_video.assert_called_once()


if __name__ == "__main__":
    unittest.main()
