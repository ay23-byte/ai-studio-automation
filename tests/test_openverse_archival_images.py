import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from core import openverse_images as ov


class OpenverseImageTests(unittest.TestCase):
    def test_only_commercially_compatible_licences_are_accepted(self):
        self.assertEqual(ov._license_label({"license": "cc0"})[0], "CC0 1.0")
        self.assertEqual(ov._license_label({"license": "by", "license_version": "4.0"})[0], "CC BY 4.0")
        self.assertEqual(ov._license_label({"license": "by-sa", "license_version": "4.0"})[0], "CC BY-SA 4.0")
        self.assertIsNone(ov._license_label({"license": "by-nc"}))
        self.assertIsNone(ov._license_label({"license": "by-nd"}))

    def test_search_query_extracts_visual_idea_from_prompt(self):
        self.assertEqual(
            ov._search_query("Create a visual. Visual idea: Indian rupee currency exchange market. Art direction: cinematic. Spoken line: unrelated narration."),
            "Indian rupee currency exchange market",
        )

    def test_search_query_uses_spoken_line_when_no_visual_idea(self):
        query = ov._search_query("Create a visual that directly illustrates this narration. Spoken line: The Hindu noted that the rupee rose six paise against the US dollar.")
        self.assertIn("rupee", query.lower())
        self.assertIn("dollar", query.lower())
        self.assertLessEqual(len(query.split()), 10)

    @patch.object(ov.requests, "post")
    @patch.object(ov.requests, "get")
    def test_oauth_token_is_used_for_openverse_search(self, get, post):
        import os
        os.environ["OPENVERSE_CLIENT_ID"] = "client"
        os.environ["OPENVERSE_CLIENT_SECRET"] = "secret"
        ov._TOKEN.update(value="", expires_at=0.0)
        token = Mock(status_code=200)
        token.json.return_value = {"access_token": "test-token", "expires_in": 36000}
        token.raise_for_status = Mock()
        post.return_value = token
        response = Mock(status_code=200)
        response.json.return_value = {"results": []}
        response.raise_for_status = Mock()
        get.return_value = response
        try:
            self.assertEqual(ov._search("rupee dollar"), [])
            self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")
            self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
            self.assertEqual(get.call_args.kwargs["params"]["page_size"], 20)
        finally:
            os.environ.pop("OPENVERSE_CLIENT_ID", None)
            os.environ.pop("OPENVERSE_CLIENT_SECRET", None)
            ov._TOKEN.update(value="", expires_at=0.0)

    def test_relevance_uses_subject_terms(self):
        item = {"title": "Apollo 11 lunar module on the Moon", "creator": "NASA"}
        self.assertGreater(ov._relevance("Apollo 11 lunar module", item), 0.5)
        self.assertEqual(ov._relevance("Apollo 11 lunar module", {"title": "Tropical beach"}), 0.0)

    @patch.object(ov.requests, "get")
    @patch("core.openverse_images.fetch_openverse_image")
    @patch("core.images._pollinations", return_value=False)
    @patch("core.images._pixabay_image", return_value=False)
    @patch("core.images._cloudflare", return_value=False)
    def test_news_image_generation_uses_openverse_before_synthetic_fallback(
        self, cloudflare, pixabay, pollinations, openverse, get
    ):
        from core.images import generate_image
        openverse.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "news-scene.jpg"
            self.assertTrue(generate_image("semiconductor fabrication cleanroom", target, "technology"))
        openverse.assert_called_once()
        pollinations.assert_not_called()

    @patch("core.openverse_images.fetch_openverse_image")
    @patch("core.images._pollinations", return_value=False)
    @patch("core.images._pixabay_image", return_value=False)
    @patch("core.images._cloudflare", return_value=False)
    def test_archival_request_never_falls_through_to_openverse_or_synthetic(
        self, cloudflare, pixabay, pollinations, openverse
    ):
        from core.images import generate_image
        with patch("core.archival_images.fetch_archival_image", return_value=False):
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "archive.jpg"
                self.assertFalse(generate_image("genuine archival newsreel footage", target, "history"))
        openverse.assert_not_called()
        pollinations.assert_not_called()

    @patch.object(ov.requests, "get")
    def test_download_writes_provenance_sidecar(self, get):
        buffer = io.BytesIO()
        Image.new("RGB", (1200, 800), "white").save(buffer, format="PNG")
        api = Mock(status_code=200)
        api.headers = {"content-type": "application/json"}
        api.json.return_value = {"results": [{
            "id": "openverse-1", "title": "Apollo 11 lunar module",
            "creator": "NASA", "url": "https://media.example/apollo.png",
            "foreign_landing_url": "https://example.org/apollo",
            "license": "cc0", "license_version": "1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "attribution": "Apollo image by NASA",
        }]}
        media = Mock()
        media.__enter__ = Mock(return_value=media)
        media.__exit__ = Mock(return_value=False)
        media.iter_content.return_value = [buffer.getvalue()]
        media.raise_for_status = Mock()
        get.side_effect = [api, media]

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "apollo.png"
            used = set()
            self.assertTrue(ov.fetch_openverse_image("Apollo 11 lunar module", target, used))
            self.assertTrue(target.is_file())
            sidecar = json.loads(target.with_suffix(".png.source.json").read_text())
            self.assertEqual(sidecar["provider"], "Openverse")
            self.assertEqual(sidecar["licence"], "CC0 1.0")
            self.assertEqual(sidecar["archival_authenticity_status"], "not_verified")
            self.assertIn("openverse-1", used)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["license_type"], "commercial")


if __name__ == "__main__":
    unittest.main()
