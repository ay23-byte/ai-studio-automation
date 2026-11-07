import unittest
from unittest.mock import Mock, patch

from core import images


class CoverrAdapterTests(unittest.TestCase):
    def setUp(self):
        images._DISABLED_VIDEO_PROVIDERS.discard("coverr")

    @patch.object(images.requests, "get")
    def test_search_uses_official_api_and_api_key(self, get):
        images.COVERR_API_KEY = "test-key"
        get.return_value = Mock(status_code=200, json=lambda: {"hits": [{"id": "abc"}]})
        hits = images._search_coverr("mountain village")
        self.assertEqual(hits, [{"id": "abc"}])
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.coverr.co/videos")
        self.assertEqual(kwargs["headers"]["x-api-key"], "test-key")
        self.assertEqual(kwargs["params"]["query"], "mountain village")
        images.COVERR_API_KEY = ""

    @patch.object(images.requests, "get")
    def test_forbidden_response_disables_coverr_for_remaining_scenes(self, get):
        images.COVERR_API_KEY = "test-key"
        get.return_value = Mock(status_code=403)
        self.assertEqual(images._search_coverr("first scene"), [])
        self.assertEqual(images._search_coverr("second scene"), [])
        get.assert_called_once()
        self.assertIn("coverr", images._DISABLED_VIDEO_PROVIDERS)
        images.COVERR_API_KEY = ""

    def test_candidate_requires_secure_download_url_and_id(self):
        self.assertIsNone(images._coverr_candidate(
            {"id": "no-url", "mp4": "http://insecure.example/video.mp4"},
            "mountain village",
        ))
        self.assertIsNone(images._coverr_candidate(
            {"mp4": "https://cdn.example/video.mp4"},
            "mountain village",
        ))

    def test_candidate_records_provider_and_human_review(self):
        candidate = images._coverr_candidate(
            {
                "id": "clip-1",
                "title": "Mountain village aerial",
                "description": "Aerial footage of a mountain village",
                "mp4_download": "https://cdn.example/video.mp4",
                "url": "https://coverr.co/videos/mountain-village",
            },
            "mountain village",
        )
        self.assertIsNotNone(candidate)
        url, metadata = candidate
        self.assertEqual(url, "https://cdn.example/video.mp4")
        self.assertEqual(metadata["provider"], "Coverr")
        self.assertGreater(metadata["relevance_score"], 0)
        self.assertTrue(metadata["human_visual_inspection_required"])
        self.assertFalse(metadata["terms_reviewed"])
        from core.asset_licensing import licence_is_usable
        self.assertTrue(licence_is_usable(metadata)[0])


if __name__ == "__main__":
    unittest.main()
