import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import uploader


class PrivateUploadTests(unittest.TestCase):
    @patch("core.uploader.MediaFileUpload")
    @patch("core.uploader._creds")
    @patch("core.uploader.build")
    @patch("core.uploader._publish_time")
    def test_private_upload_ignores_configured_publish_time(
        self, publish_time, build, creds, media
    ):
        publish_time.return_value = "2030-01-01T12:00:00.000Z"
        request = MagicMock()
        request.next_chunk.side_effect = [(None, {"id": "video123"})]
        youtube = MagicMock()
        youtube.videos.return_value.insert.return_value = request
        build.return_value = youtube

        video_id = uploader.upload(
            Path("review_short.mp4"), "Title", "Description", ["news"],
            privacy_status="private", disable_scheduling=True
        )

        self.assertEqual(video_id, "video123")
        _, kwargs = youtube.videos.return_value.insert.call_args
        self.assertEqual(kwargs["body"]["status"]["privacyStatus"], "private")
        self.assertNotIn("publishAt", kwargs["body"]["status"])
        publish_time.assert_not_called()

    @patch("core.uploader._creds")
    @patch("core.uploader.build")
    def test_existing_private_upload_is_found_by_source_marker(self, build, creds):
        marker = "AI_STUDIO_SOURCE_RUN_ID:12345"
        youtube = MagicMock()
        youtube.search.return_value.list.return_value.execute.return_value = {"items": [{"id": {"videoId": "existing123"}}]}
        youtube.videos.return_value.list.return_value.execute.return_value = {"items": [{"snippet": {"description": marker}, "status": {"privacyStatus": "private"}}]}
        build.return_value = youtube
        self.assertEqual(uploader.find_private_video_by_marker(marker), "existing123")

    @patch("core.uploader._creds")
    @patch("core.uploader.build")
    def test_existing_non_private_marker_blocks_duplicate_upload(self, build, creds):
        marker = "AI_STUDIO_SOURCE_RUN_ID:12345"
        youtube = MagicMock()
        youtube.search.return_value.list.return_value.execute.return_value = {"items": [{"id": {"videoId": "existing123"}}]}
        youtube.videos.return_value.list.return_value.execute.return_value = {"items": [{"snippet": {"description": marker}, "status": {"privacyStatus": "public"}}]}
        build.return_value = youtube
        with self.assertRaisesRegex(RuntimeError, "not private"):
            uploader.find_private_video_by_marker(marker)

    @patch("core.uploader._creds")
    @patch("core.uploader.build")
    def test_upload_marker_search_error_fails_closed(self, build, creds):
        youtube = MagicMock()
        youtube.search.return_value.list.return_value.execute.side_effect = RuntimeError("API unavailable")
        build.return_value = youtube
        with self.assertRaisesRegex(RuntimeError, "API unavailable"):
            uploader.find_private_video_by_marker("AI_STUDIO_SOURCE_RUN_ID:12345")


if __name__ == "__main__":
    unittest.main()
