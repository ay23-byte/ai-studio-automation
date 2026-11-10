import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.images import _cloudflare, _pollinations


class _Response:
    def __init__(self, content=b"fake-image", content_type="image/png"):
        self.content = content
        self.headers = {"content-type": content_type}
        self.status_code = 200

    def raise_for_status(self):
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


if __name__ == "__main__":
    unittest.main()
