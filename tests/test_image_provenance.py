import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from core.images import _cloudflare, _pollinations, _pixabay_image, _POLLINATIONS_STATE


class _Response:
    def __init__(self, content=b"fake-image", content_type="image/png", payload=None):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = 200
        self.payload = payload

    def json(self):
        return self.payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)
        return None


class ImageProvenanceTests(unittest.TestCase):
    @patch.dict(os.environ, {"FREE_ONLY_MODE": "0", "CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "token"})
    @patch("core.images.requests.post")
    def test_cloudflare_is_hard_disabled_even_with_credentials(self, post):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "generated.jpg"
            self.assertFalse(_cloudflare("documentary visual prompt", path))
            self.assertFalse(path.exists())
        post.assert_not_called()

    def test_pixabay_still_fallback_requires_relevance_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scene.jpg"
            api = _Response(content=b"", content_type="application/json", payload={"hits": [{
                "tags": "rocket launch coastal launchpad space",
                "largeImageURL": "https://pixabay.example/rocket.jpg",
                "pageURL": "https://pixabay.com/photos/rocket-launch-123/",
                "user": "creator",
            }]})
            image = _Response(content=b"jpeg-image", content_type="image/jpeg")
            with patch("core.images.PIXABAY_API_KEY", "test-key"), \
                 patch("core.images.requests.get", side_effect=[api, image]) as get, \
                 patch("core.images._ok", return_value=True):
                self.assertTrue(_pixabay_image("A rocket launch from a coastal launchpad", path))
            self.assertEqual(get.call_count, 2)
            sidecar = json.loads(path.with_suffix(".jpg.source.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["provider"], "Pixabay")
            self.assertEqual(sidecar["page_url"], "https://pixabay.com/photos/rocket-launch-123/")
            self.assertGreaterEqual(sidecar["relevance_score"], 0.12)

    def test_pixabay_rejects_unrelated_images_instead_of_using_generic_filler(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scene.jpg"
            api = _Response(content=b"", content_type="application/json", payload={"hits": [{
                "tags": "cats kittens pet",
                "largeImageURL": "https://pixabay.example/cats.jpg",
            }]})
            with patch("core.images.PIXABAY_API_KEY", "test-key"), \
                 patch("core.images.requests.get", return_value=api) as get:
                self.assertFalse(_pixabay_image("A rocket launch from a coastal launchpad", path))
            get.assert_called_once()
            self.assertFalse(path.exists())

    def test_pollinations_generated_image_gets_provenance_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "generated.jpg"
            with patch("core.images.POLLINATIONS_GAP", 0), patch(
                "core.images.requests.get", return_value=_Response()
            ), patch("core.images._ok", return_value=True):
                self.assertTrue(_pollinations("documentary visual prompt", path))
            sidecar = json.loads(path.with_suffix(".jpg.source.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["provider"], "Pollinations")
            self.assertEqual(sidecar["model"], "flux")
            self.assertIn("prompt_sha256", sidecar)


    def test_pollinations_payment_required_disables_provider_for_rest_of_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "generated.jpg"
            _POLLINATIONS_STATE["failures"] = 0
            response = _Response(content=b"", content_type="application/json")
            response.status_code = 402
            with patch("core.images.POLLINATIONS_GAP", 0), patch(
                "core.images.requests.get", return_value=response
            ) as get:
                self.assertFalse(_pollinations("first scene", path))
                self.assertFalse(_pollinations("second scene", path))
            self.assertEqual(get.call_count, 1)
            self.assertFalse(path.exists())
            _POLLINATIONS_STATE["failures"] = 0


if __name__ == "__main__":
    unittest.main()
