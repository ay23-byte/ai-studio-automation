import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from core import wikimedia_images as wm


class WikimediaImageTests(unittest.TestCase):
    @patch.object(wm.requests, "get")
    def test_search_downloads_relevant_cc_image_and_writes_provenance(self, get):
        buffer = io.BytesIO()
        Image.new("RGB", (1200, 800), "white").save(buffer, format="PNG")
        api = Mock(status_code=200)
        api.raise_for_status = Mock()
        api.json.return_value = {"query": {"pages": {"1": {
            "pageid": 1, "title": "File:Indian rupee banknotes.jpg",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/example.png",
                "thumburl": "https://upload.wikimedia.org/thumb.png",
                "mime": "image/png", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Indian_rupee.jpg",
                "extmetadata": {
                    "ObjectName": {"value": "Indian rupee banknotes"},
                    "Artist": {"value": "Example photographer"},
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                    "ImageDescription": {"value": "Indian currency notes"},
                    "Credit": {"value": "Example photographer / Wikimedia Commons"},
                },
            }],
        }}}}
        media = Mock()
        media.__enter__ = Mock(return_value=media)
        media.__exit__ = Mock(return_value=False)
        media.iter_content.return_value = [buffer.getvalue()]
        media.raise_for_status = Mock()
        get.side_effect = [api, media]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "rupee.png"
            self.assertTrue(wm.fetch_wikimedia_image("Indian rupee banknotes", target))
            sidecar = json.loads(target.with_suffix(".png.source.json").read_text())
            self.assertEqual(sidecar["provider"], "Wikimedia Commons")
            self.assertEqual(sidecar["licence"], "CC BY-SA 4.0")
            self.assertEqual(sidecar["page_url"], "https://commons.wikimedia.org/wiki/File:Indian_rupee.jpg")

    def test_rejects_noncommercial_and_unlinked_licences(self):
        self.assertIsNone(wm._licence({"extmetadata": {
            "LicenseShortName": {"value": "CC BY-NC 4.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-nc/4.0/"},
        }}))
        self.assertIsNone(wm._licence({"extmetadata": {
            "LicenseShortName": {"value": "CC BY 4.0"}, "LicenseUrl": {"value": ""},
        }}))

    def test_query_extracts_spoken_line_without_prompt_boilerplate(self):
        q = wm._query("Create a visual. Spoken line: The Hindu noted that the rupee rose against the US dollar.")
        self.assertIn("rupee", q.lower())
        self.assertIn("dollar", q.lower())
        self.assertLessEqual(len(q.split()), 10)


if __name__ == "__main__":
    unittest.main()
