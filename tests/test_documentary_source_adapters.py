import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import footage_sources as fs


class DocumentarySourceAdapterTests(unittest.TestCase):
    def setUp(self):
        fs._DISABLED_PROVIDER_HOSTS.clear()

    @patch.object(fs.requests, "get")
    def test_forbidden_provider_is_not_queried_repeatedly(self, get):
        get.return_value = Mock(status_code=403)
        self.assertIsNone(fs._get_json("https://api.dvidshub.net/search", {"q": "one"}))
        self.assertIsNone(fs._get_json("https://api.dvidshub.net/search", {"q": "two"}))
        get.assert_called_once()
        self.assertIn("api.dvidshub.net", fs._DISABLED_PROVIDER_HOSTS)

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_rate_limited_provider_retries_then_recovers(self, get, sleep):
        recovered = Mock(status_code=200)
        recovered.headers = {"content-type": "application/json"}
        recovered.json.return_value = {"results": ["clip"]}
        limited = Mock(status_code=429)
        limited.headers = {"Retry-After": "0"}
        get.side_effect = [limited, recovered]

        result = fs._get_json("https://www.loc.gov/videos/", {"q": "documentary"})

        self.assertEqual(result, {"results": ["clip"]})
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()
        self.assertNotIn("www.loc.gov", fs._DISABLED_PROVIDER_HOSTS)

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_persistent_rate_limit_opens_provider_circuit(self, get, sleep):
        limited = Mock(status_code=429)
        limited.headers = {}
        get.return_value = limited

        self.assertIsNone(fs._get_json("https://www.loc.gov/videos/", {"q": "one"}))
        self.assertEqual(get.call_count, 3)
        self.assertIn("www.loc.gov", fs._DISABLED_PROVIDER_HOSTS)
        self.assertIsNone(fs._get_json("https://www.loc.gov/videos/", {"q": "two"}))
        self.assertEqual(get.call_count, 3)

    @patch("core.config.NARA_API_KEY", "nara-test")
    @patch.object(fs, "_get_json")
    def test_nara_requires_explicit_public_domain_and_video_object(self, get_json):
        get_json.return_value = {"body": {"hits": {"hits": [
            {"_id": "101", "_source": {"record": {
                "naId": 101, "title": "Government film",
                "useRestriction": {"status": "Unrestricted"},
                "copyrightNote": "This is a work of the U.S. Federal Government and is public domain.",
                "digitalObjects": [{"objectUrl": "https://archives.gov/a.mp4", "objectType": "Video File"}],
            }}},
            {"_id": "102", "_source": {"record": {
                "naId": 102, "title": "Donated film",
                "useRestriction": {"status": "Unrestricted"},
                "copyrightNote": "Copyright status not evaluated.",
                "digitalObjects": [{"objectUrl": "https://archives.gov/b.mp4", "objectType": "Video File"}],
            }}},
        ]}}}
        result = fs._nara("government film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["asset_id"], "101")
        self.assertEqual(result[0][1]["licence"], "Public Domain")
        self.assertEqual(get_json.call_args.args[0], "https://catalog.archives.gov/api/v2/records/search")
        self.assertEqual(get_json.call_args.args[2]["x-api-key"], "nara-test")

    @patch.dict(os.environ, {"DVIDS_API_KEY": "dvids-test"})
    @patch.object(fs, "_get_json")
    def test_dvids_requires_item_rights_and_download(self, get_json):
        get_json.return_value = {"results": [
            {"id": "video:7", "type": "video", "title": "Training exercise", "credit": "Creator",
             "hls_url": "https://api.dvidshub.net/hls/video/7.m3u8?api_key=test",
             "url": "https://www.dvidshub.net/video/7/training"},
            {"id": "image:8", "type": "image", "title": "Not video",
             "url": "https://www.dvidshub.net/image/8/restricted"},
        ]}
        result = fs._dvids("training")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["provider"], "DVIDS")
        self.assertEqual(result[0][1]["licence"], "DVIDS API Commercial Use Terms")

    @patch("core.config.EUROPEANA_API_KEY", "eu-test")
    @patch.object(fs, "_get_json")
    def test_europeana_accepts_only_supported_cc_rights(self, get_json):
        get_json.return_value = {"items": [
            {"id": "/x/1", "guid": "https://europeana.eu/item/1",
             "title": ["Open film"], "dcCreator": ["Creator"],
             "rights": ["http://creativecommons.org/licenses/by/4.0/"],
             "edmIsShownBy": ["https://media.example/film.mp4"]},
            {"id": "/x/2", "rights": ["http://rightsstatements.org/vocab/InC/1.0/"],
             "edmIsShownBy": ["https://media.example/restricted.mp4"]},
        ]}
        result = fs._europeana_cc("open film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["licence"], "CC BY 4.0")

    @patch.object(fs, "_get_json")
    def test_nasa_adapter_checks_video_manifest_and_rights(self, get_json):
        get_json.side_effect = [
            {"collection": {"items": [{
                "data": [{"nasa_id": "N-1", "title": "Apollo launch",
                          "description": "Public domain NASA footage"}],
                "links": [{"rel": "canonical", "href": "https://images.nasa.gov/details/N-1"}],
                "href": "https://images.nasa.gov/asset/N-1/manifest",
            }]}},
            ["https://images-assets.nasa.gov/video/N-1.mp4"],
        ]
        result = fs._nasa("Apollo launch")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["provider"], "NASA")

    @patch.object(fs, "_get_json")
    def test_loc_adapter_requires_item_rights_and_video_resource(self, get_json):
        get_json.return_value = {"results": [{
            "id": "https://www.loc.gov/item/abc/",
            "title": "Historic film",
            "rights": "No known copyright restrictions",
            "resources": [{"files": [{"url": "https://loc.gov/media/film.mp4"}]}],
        }]}
        result = fs._loc("historic film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["provider"], "Library of Congress")

    @patch.object(fs, "_get_json")
    def test_wikimedia_requires_file_specific_cc_metadata(self, get_json):
        get_json.return_value = {"query": {"pages": {"1": {
            "title": "File:Open film.webm",
            "imageinfo": [{
                "url": "https://upload.wikimedia.org/open-film.webm",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Open_film.webm",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                    "Artist": {"value": "Example creator"},
                },
            }],
        }}}}
        result = fs._wikimedia("open film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["licence"], "CC BY-SA 4.0")

    @patch.object(fs, "_get_json")
    def test_internet_archive_requires_explicit_item_licence(self, get_json):
        get_json.side_effect = [
            {"response": {"docs": [{"identifier": "open-film", "title": "Open film"}]}},
            {"metadata": {"licenseurl": "https://creativecommons.org/licenses/by/4.0/"},
             "files": [{"name": "open-film.mp4", "size": "90000"}]},
        ]
        result = fs._internet_archive("open film")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1]["licence"], "CC BY 4.0")

    def test_mixkit_manual_import_rejects_restricted_and_records_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"video-placeholder")
            with self.assertRaises(ValueError):
                fs.register_mixkit_asset(source, title="Clip",
                    source_url="https://mixkit.co/free-stock-video/clip-1/",
                    licence="Mixkit Restricted License")
            # Use a temporary working directory so the import cannot touch the repo.
            old = Path.cwd()
            os.chdir(tmp)
            try:
                target, sidecar = fs.register_mixkit_asset(
                    source, title="Clip", source_url="https://mixkit.co/free-stock-video/clip-1/",
                    licence="Mixkit Free License", creator="Creator")
            finally:
                os.chdir(old)
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(metadata["licence"], "Mixkit Free License")
            self.assertEqual(metadata["page_url"], "https://mixkit.co/free-stock-video/clip-1/")
            self.assertTrue(metadata["terms_reviewed"])
            self.assertFalse(metadata["automated_download"])

    @patch.object(fs, "_download", return_value=True)
    @patch.object(fs, "_nara")
    @patch.object(fs, "_dvids")
    @patch.object(fs, "_loc")
    @patch.object(fs, "_wikimedia")
    @patch.object(fs, "_internet_archive")
    @patch.object(fs, "_europeana_cc")
    def test_nara_and_other_providers_are_in_discovery_chain(
        self, europeana, archive, wikimedia, loc, dvids, nara, download
    ):
        nara.return_value = [("https://archives.gov/a.mp4", {
            "provider": "National Archives (NARA)", "asset_id": "1",
            "title": "Mountain film", "licence": "Public Domain",
            "licence_url": "https://archives.gov/id/1"})]
        with patch.object(fs, "_metadata_relevance", return_value=1.0):
            self.assertTrue(fs.fetch_documentary_clip(["mountain film"], Path("unused.mp4"), set()))
        self.assertTrue(nara.called)
        self.assertTrue(download.called)


if __name__ == "__main__":
    unittest.main()
