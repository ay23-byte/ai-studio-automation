import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from core import archival_images as archival
from core.archival_images import fetch_archival_image


class ArchivalImageTests(unittest.TestCase):
    def setUp(self):
        archival._SEARCH_CACHE.clear()
        archival._LAST_SEARCH_AT = 0.0

    def _page(self, licence="CC BY-SA 4.0", title="File:Kolkata 1940s street.jpg",
              date="1944"):
        return {
            "pageid": 42, "title": title,
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/example.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Kolkata_1940s.jpg",
                "mime": "image/jpeg",
                "extmetadata": {
                    "LicenseShortName": {"value": licence},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                    "Artist": {"value": "Example photographer"},
                    "DateTimeOriginal": {"value": date},
                    "ImageDescription": {"value": "Street scene in Kolkata during the 1940s"},
                },
            }],
        }

    @patch("core.archival_images.requests.get")
    def test_downloads_explicitly_licensed_relevant_image_with_provenance(self, get):
        image_bytes = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        image_bytes.close()
        Image.new("RGB", (1200, 800)).save(image_bytes.name, format="JPEG")
        payload = Path(image_bytes.name).read_bytes()
        Path(image_bytes.name).unlink()

        search_response = Mock()
        search_response.json.return_value = {"query": {"pages": {"42": self._page()}}}
        search_response.raise_for_status.return_value = None
        download_response = Mock()
        download_response.__enter__ = Mock(return_value=download_response)
        download_response.__exit__ = Mock(return_value=False)
        download_response.iter_content.return_value = [payload]
        download_response.raise_for_status.return_value = None
        get.side_effect = [search_response, download_response]

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "archive.jpg"
            used = set()
            self.assertTrue(fetch_archival_image("Kolkata 1940s street", target, used))
            self.assertTrue(target.is_file())
            metadata = json.loads(target.with_suffix(".jpg.source.json").read_text())
            self.assertEqual(metadata["provider"], "Wikimedia Commons")
            self.assertEqual(metadata["date_created"], "1944")
            self.assertEqual(metadata["archival_authenticity_status"], "pending_human_verification")
            self.assertIn("42", used)

    @patch("core.archival_images.requests.get")
    def test_rejects_unknown_or_restricted_licence(self, get):
        response = Mock()
        response.json.return_value = {"query": {"pages": {"42": self._page("All rights reserved")}}}
        response.raise_for_status.return_value = None
        # Commons is queried first; Openverse is the expected fallback when
        # Commons has no item with an explicitly permitted licence.
        openverse_response = Mock()
        openverse_response.json.return_value = {"results": []}
        openverse_response.raise_for_status.return_value = None
        get.side_effect = [response, openverse_response]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fetch_archival_image("Kolkata 1940s street", Path(tmp) / "archive.jpg"))
            self.assertEqual(get.call_count, 2)
            self.assertIn("commons.wikimedia.org", get.call_args_list[0].args[0])
            self.assertIn("api.openverse.org", get.call_args_list[1].args[0])

    @patch("core.archival_images.requests.get")
    def test_rejects_irrelevant_results(self, get):
        response = Mock()
        response.json.return_value = {"query": {"pages": {"42": self._page(title="File:Unrelated landscape.jpg")}}}
        response.raise_for_status.return_value = None
        get.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fetch_archival_image("Kolkata 1940s street", Path(tmp) / "archive.jpg"))

    @patch("core.archival_images.requests.get")
    def test_successful_search_is_cached_for_repeated_query(self, get):
        response = Mock()
        response.json.return_value = {"query": {"pages": {"42": self._page()}}}
        response.raise_for_status.return_value = None
        get.return_value = response
        first = archival._search("Apollo 11")
        second = archival._search("Apollo 11")
        self.assertEqual(first, second)
        get.assert_called_once()

    def test_retry_after_supports_seconds_and_http_date(self):
        self.assertEqual(archival._retry_delay({"Retry-After": "7"}, 0), 7.0)
        self.assertGreaterEqual(archival._retry_delay({}, 0), 2.0)

    @patch("core.archival_images.requests.get")
    def test_empty_query_does_not_make_network_request(self, get):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(fetch_archival_image(" ", Path(tmp) / "archive.jpg"))
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
