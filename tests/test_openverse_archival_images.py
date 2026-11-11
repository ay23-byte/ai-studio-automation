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

    def test_relevance_uses_subject_terms(self):
        item = {"title": "Apollo 11 lunar module on the Moon", "creator": "NASA"}
        self.assertGreater(ov._relevance("Apollo 11 lunar module", item), 0.5)
        self.assertEqual(ov._relevance("Apollo 11 lunar module", {"title": "Tropical beach"}), 0.0)

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
