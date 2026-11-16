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
            "description": "Apollo 11 Saturn V launch from Kennedy Space Center on July 16, 1969.",
            "tags": ["Apollo 11", "Saturn V", "launch", "Kennedy Space Center", "1969"],
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


class NASASearchReachTests(unittest.TestCase):
    """A space documentary asks NASA for every scene, and long queries are searched again shorter."""

    def setUp(self):
        from core import footage_sources
        footage_sources.reset_run_state()
        self.addCleanup(footage_sources.reset_run_state)

    def _names(self, context):
        from core.footage_sources import archive_providers
        return [provider.name for provider in archive_providers(context)]

    def test_scene_without_a_space_word_gets_nasa_when_the_documentary_is_about_space(self):
        from core import footage_sources
        scene = "gold hexagonal mirror segments unfolding in a clean room"
        self.assertNotIn("NASA", self._names(scene))
        footage_sources.set_run_subject("How the James Webb Space Telescope Sees the First Galaxies", "science")
        self.assertIn("NASA", self._names(scene))
        footage_sources.reset_run_state()
        self.assertNotIn("NASA", self._names(scene))                 # the subject does not leak into the next run

    def test_plural_and_mission_words_select_nasa(self):
        for context in ("distant galaxies in infrared light", "JWST sunshield", "stars forming in a nebula"):
            self.assertIn("NASA", self._names(context), context)
        self.assertNotIn("NASA", self._names("farmers market in a village square"))

    def test_shorter_query_keeps_the_first_subject_words(self):
        from core.footage_sources import _shorter_query
        self.assertEqual(_shorter_query("James Webb Space Telescope sunshield deployment test"), "James Webb Space")
        self.assertEqual(_shorter_query("Apollo 11 launch"), "")                    # already short
        self.assertEqual(_shorter_query("archival film of Ariane rocket launch Kourou"), "Ariane rocket launch")

    @patch("core.footage_sources._get_json")
    def test_a_long_query_with_no_results_is_searched_again_shorter(self, get_json):
        item = NASAArchivalRightsTests()._item("James Webb Space Telescope sunshield deployment test at Northrop.")
        item["collection"]["items"][0]["data"][0].update(title="Webb sunshield deployment test", center="GSFC")
        get_json.side_effect = [
            {"collection": {"items": []}},
            item,
            ["https://images-assets.nasa.gov/video/KSC-1969-001/KSC-1969-001~orig.mp4"],
        ]
        found = _nasa("James Webb Space Telescope sunshield deployment test")
        self.assertEqual(len(found), 1)
        self.assertEqual(get_json.call_args_list[0].args[1]["q"], "James Webb Space Telescope sunshield deployment test")
        self.assertEqual(get_json.call_args_list[1].args[1]["q"], "James Webb Space")
        self.assertEqual(found[0][1]["query"], "James Webb Space Telescope sunshield deployment test")
        self.assertEqual(found[0][1]["searched_as"], "James Webb Space")

    @patch("core.footage_sources._get_json")
    def test_a_failed_request_is_not_repeated_shorter(self, get_json):
        get_json.return_value = None                                  # provider error or switched off
        self.assertEqual(_nasa("James Webb Space Telescope sunshield deployment test"), [])
        self.assertEqual(get_json.call_count, 1)
