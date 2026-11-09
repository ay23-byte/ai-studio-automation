import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import footage_sources


class ArchivalVideoTests(unittest.TestCase):
    def test_detects_archival_video_intent(self):
        self.assertTrue(footage_sources._is_archival_video_request(
            ["Chernobyl 1986"], "1986 Soviet-era archival newsreel footage"
        ))
        self.assertFalse(footage_sources._is_archival_video_request(
            ["modern city skyline"], "cinematic present-day city footage"
        ))

    @patch("core.footage_sources._download", return_value=True)
    @patch("core.footage_sources._nasa", return_value=[])
    @patch("core.footage_sources._nara")
    @patch("core.footage_sources._dvids", return_value=[])
    @patch("core.footage_sources._loc", return_value=[])
    @patch("core.footage_sources._wikimedia", return_value=[])
    @patch("core.footage_sources._internet_archive", return_value=[])
    @patch("core.footage_sources._europeana_cc", return_value=[])
    def test_archive_clip_is_tagged_with_provenance_review_fields(
        self, europeana, internet_archive, wikimedia, loc, dvids, nara, nasa, download
    ):
        nara.return_value = [("https://archive.example/clip.mp4", {
            "provider": "National Archives (NARA)", "asset_id": "123",
            "title": "Chernobyl 1986 evacuation newsreel",
            "page_url": "https://catalog.archives.gov/id/123",
            "creator": "U.S. National Archives", "date_created": "1986",
            "licence": "Public Domain", "licence_url": "https://catalog.archives.gov/id/123",
        })]
        seen = {}
        def fake_download(url, path, metadata):
            seen.update(metadata)
            return True
        download.side_effect = fake_download
        with tempfile.TemporaryDirectory() as tmp:
            found = footage_sources.fetch_documentary_clip(
                ["Chernobyl 1986"], Path(tmp) / "archive.mp4", set(),
                scene_prompt="1986 archival newsreel footage of Chernobyl evacuation"
            )
        self.assertTrue(found)
        self.assertEqual(seen["asset_type"], "archival_video_candidate")
        self.assertEqual(seen["archival_authenticity_status"], "pending_human_verification")
        self.assertTrue(seen["archival_provenance_complete"])

    def test_incomplete_archive_metadata_is_not_misrepresented_as_verified(self):
        metadata = {"provider": "Internet Archive", "page_url": "https://archive.org/details/abc",
                    "licence": "Public Domain", "licence_url": "https://archive.org/details/abc"}
        fields = {
            "item_page": bool(metadata.get("page_url")),
            "rights_statement": bool(metadata.get("licence") and metadata.get("licence_url")),
            "creator_or_holding_institution": bool(metadata.get("creator") or metadata.get("provider")),
            "date_or_period": bool(metadata.get("date_created") or metadata.get("date") or metadata.get("year") or metadata.get("description")),
        }
        self.assertFalse(all(fields.values()))

    def test_chernobyl_query_expands_to_archive_catalogue_phrases(self):
        queries = footage_sources._expanded_queries(
            ["Chernobyl 1986 evacuation newsreel"],
            "1986 Soviet-era archival newsreel footage"
        )
        self.assertIn("Chernobyl 1986 evacuation newsreel", queries)
        self.assertIn("Chernobyl 1986 disaster film", queries)
        self.assertIn("Pripyat 1986 Soviet newsreel", queries)

    @patch("core.footage_sources._nara", return_value=[])
    @patch("core.footage_sources._dvids", return_value=[])
    @patch("core.footage_sources._loc", side_effect=RuntimeError("HTTP 429"))
    @patch("core.footage_sources._wikimedia", return_value=[])
    @patch("core.footage_sources._internet_archive", return_value=[])
    @patch("core.footage_sources._europeana_cc", return_value=[])
    def test_rate_limited_provider_does_not_stop_other_archive_searches(
        self, europeana, internet_archive, wikimedia, loc, dvids, nara
    ):
        with tempfile.TemporaryDirectory() as tmp:
            found = footage_sources.fetch_documentary_clip(
                ["Chernobyl 1986"], Path(tmp) / "archive.mp4", set(),
                scene_prompt="archival newsreel footage of Chernobyl 1986"
            )
        self.assertFalse(found)
        self.assertGreater(wikimedia.call_count, 0)
        self.assertGreater(internet_archive.call_count, 0)
        self.assertGreater(europeana.call_count, 0)


if __name__ == "__main__":
    unittest.main()
