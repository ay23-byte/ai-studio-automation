import unittest
from unittest.mock import patch

from core.footage_sources import _nasa
from core.asset_licensing import licence_is_usable


class NASAArchivalRightsTests(unittest.TestCase):
    def _item(self, description):
        return {
            "collection": {
                "items": [{
                    "data": [{
                        "nasa_id": "KSC-1969-001",
                        "title": "Apollo 11 Launch",
                        "description": description,
                        "keywords": ["Apollo 11", "Saturn V", "1969"],
                        "center": "KSC",
                    }],
                    "links": [{"rel": "canonical", "href": "https://images.nasa.gov/details/KSC-1969-001"}],
                    "href": "https://images-assets.nasa.gov/video/KSC-1969-001/collection.json",
                }]
            }
        }

    @patch("core.footage_sources._get_json")
    def test_nasa_center_item_without_third_party_credit_is_discoverable(self, get_json):
        get_json.side_effect = [
            self._item("Apollo 11 Saturn V launch from Kennedy Space Center, July 16, 1969."),
            ["https://images-assets.nasa.gov/video/KSC-1969-001/KSC-1969-001~orig.mp4"],
        ]
        found = _nasa("Apollo 11 launch 1969")
        self.assertEqual(len(found), 1)
        metadata = found[0][1]
        self.assertEqual(metadata["licence"], "NASA Media Usage Guidelines")
        self.assertEqual(metadata["creator"], "NASA KSC")
        self.assertTrue(licence_is_usable(metadata)[0])

    @patch("core.footage_sources._get_json")
    def test_nasa_item_with_third_party_credit_is_rejected(self, get_json):
        get_json.side_effect = [
            self._item("Apollo 11 launch. Copyright © Getty Images."),
            ["https://images-assets.nasa.gov/video/KSC-1969-001/KSC-1969-001~orig.mp4"],
        ]
        self.assertEqual(_nasa("Apollo 11 launch 1969"), [])


    @patch("core.footage_sources._download", return_value=True)
    @patch("core.footage_sources._europeana_cc", return_value=[])
    @patch("core.footage_sources._internet_archive", return_value=[])
    @patch("core.footage_sources._wikimedia", return_value=[])
    @patch("core.footage_sources._loc", return_value=[])
    @patch("core.footage_sources._dvids", return_value=[])
    @patch("core.footage_sources._nara", return_value=[])
    @patch("core.footage_sources._nasa")
    def test_nasa_guidelines_candidate_passes_download_licence_gate(
        self, nasa, _nara, _dvids, _loc, _wikimedia, _internet_archive,
        _europeana_cc, download
    ):
        metadata = {
            "provider": "NASA",
            "asset_id": "KSC-1969-001",
            "title": "Apollo 11 Launch",
            "page_url": "https://images.nasa.gov/details/KSC-1969-001",
            "licence": "NASA Media Usage Guidelines",
            "licence_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
        }
        nasa.return_value = [(
            "https://images-assets.nasa.gov/video/KSC-1969-001/KSC-1969-001~orig.mp4",
            metadata,
        )]
        from core.footage_sources import fetch_documentary_clip
        found = fetch_documentary_clip(
            ["Apollo 11 launch"], "build/test-nasa-archival.mp4", set(),
            scene_prompt="genuine archival footage Apollo 11 launch",
        )
        self.assertTrue(found)
        download.assert_called_once()



if __name__ == "__main__":
    unittest.main()
