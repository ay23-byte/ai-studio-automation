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


if __name__ == "__main__":
    unittest.main()
