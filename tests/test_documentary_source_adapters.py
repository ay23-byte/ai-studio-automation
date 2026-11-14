import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import footage_sources as fs


class DocumentarySourceAdapterTests(unittest.TestCase):
    def setUp(self):
        fs.reset_run_state()
        self.addCleanup(fs.reset_run_state)
        pacing = patch.dict(os.environ, {"FOOTAGE_PACING_FACTOR": "0"})
        pacing.start()
        self.addCleanup(pacing.stop)

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
        # Names from NARA's published OpenAPI description; "rows" is not one of them.
        params = get_json.call_args.args[1]
        self.assertEqual(params["typeOfMaterials"], "Moving Images")
        self.assertEqual(params["availableOnline"], "true")
        self.assertIn("limit", params)
        self.assertNotIn("rows", params)

    # The fixtures below copy the response shapes the providers' live APIs returned
    # on 2026-11-14 (field names and nesting), reduced to the fields the adapters read.

    @patch.dict(os.environ, {"DVIDS_API_KEY": "dvids-test"})
    @patch.object(fs, "_get_json")
    def test_dvids_reads_media_files_from_the_asset_endpoint(self, get_json):
        # /search returns no media links; /asset does.
        search = {"results": [
            {"id": "video:7", "type": "video", "title": "Training exercise", "credit": "Sgt. Creator",
             "short_description": "Soldiers conduct a training exercise", "keywords": "training,exercise",
             "duration": 61, "url": "https://www.dvidshub.net/video/7/training"},
            {"id": "image:8", "type": "image", "title": "Not video",
             "url": "https://www.dvidshub.net/image/8/restricted"},
        ]}
        asset = {"results": {
            "id": "video:7", "title": "Training exercise", "duration": 61,
            "credit": [{"name": "Sgt. Creator"}], "unit_name": "1st Brigade", "branch": "Army",
            "url": "https://www.dvidshub.net/video/7/training",
            "files": [
                {"src": "https://d34w7g4gy10iej.cloudfront.net/video/7/DOD_7-1920x1080-9000k.mp4",
                 "type": "video/mp4", "width": 1920, "height": 1080, "size": 40_000_000, "bitrate": 9000},
                {"src": "https://d34w7g4gy10iej.cloudfront.net/video/7/DOD_7-1280x720-3000k.mp4",
                 "type": "video/mp4", "width": 1280, "height": 720, "size": 15_000_000, "bitrate": 3000},
            ],
        }}
        get_json.side_effect = [search, asset]
        result = fs._dvids("training exercise")
        self.assertEqual(len(result), 1)
        url, metadata = result[0]
        self.assertTrue(url.endswith("1920x1080-9000k.mp4"))
        self.assertEqual(metadata["provider"], "DVIDS")
        self.assertEqual(metadata["licence"], "DVIDS API Commercial Use Terms")
        self.assertEqual(metadata["creator"], "Sgt. Creator")
        self.assertEqual(metadata["licence_state"], "eligible_with_attribution")
        self.assertEqual(len(metadata["alternate_urls"]), 1)
        search_call, asset_call = get_json.call_args_list
        self.assertEqual(search_call.args[0], "https://api.dvidshub.net/search")
        self.assertEqual(search_call.args[1]["api_key"], "dvids-test")
        self.assertEqual(asset_call.args[0], "https://api.dvidshub.net/asset")
        self.assertEqual(asset_call.args[1]["id"], "video:7")
        # No Referer is invented: DVIDS rejects a key used from an origin it was not registered for.
        self.assertIsNone(search_call.args[2])

    @patch.dict(os.environ, {"DVIDS_API_KEY": "dvids-test", "DVIDS_API_REFERER": "https://example.org"})
    @patch.object(fs, "_get_json", return_value={"results": []})
    def test_dvids_sends_the_registered_origin_when_one_is_configured(self, get_json):
        fs._dvids("training")
        self.assertEqual(get_json.call_args.args[2]["Referer"], "https://example.org")

    @patch("core.config.EUROPEANA_API_KEY", "eu-test")
    @patch.object(fs, "_get_json")
    def test_europeana_accepts_only_supported_cc_rights(self, get_json):
        get_json.return_value = {"items": [
            {"id": "/x/1", "guid": "https://www.europeana.eu/item/x/1?utm_source=api",
             "title": ["Open film"], "dcCreator": ["Creator"],
             "rights": ["http://creativecommons.org/licenses/by/4.0/"],
             "edmIsShownBy": ["https://media.example/film.mp4"]},
            {"id": "/x/2", "rights": ["http://rightsstatements.org/vocab/InC/1.0/"],
             "edmIsShownBy": ["https://media.example/restricted.mp4"]},
            {"id": "/x/3", "title": ["Old newsreel"], "dataProvider": ["Sound & Vision"],
             "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
             "edmIsShownBy": ["https://media.example/newsreel.mp4"]},
            {"id": "/x/4", "title": ["Web page only"],
             "rights": ["http://creativecommons.org/licenses/by-sa/3.0/"],
             "edmIsShownBy": ["https://media.example/player?id=4"]},
        ]}
        result = fs._europeana_cc("open film")
        self.assertEqual([item[1]["licence"] for item in result], ["CC BY 4.0", "Public Domain"])
        self.assertEqual(result[0][1]["page_url"], "https://www.europeana.eu/item/x/1")
        params, headers = get_json.call_args.args[1], get_json.call_args.args[2]
        self.assertEqual(headers, {"X-Api-Key": "eu-test"})
        self.assertNotIn("wskey", params)
        self.assertEqual(params["reusability"], "open")
        self.assertEqual(params["qf"], "TYPE:VIDEO")

    @patch.object(fs, "_get_json")
    def test_nasa_adapter_checks_video_manifest_and_rights(self, get_json):
        get_json.side_effect = [
            {"collection": {"items": [{
                "data": [{"nasa_id": "N-1", "title": "Apollo launch", "center": "KSC",
                          "description": "Public domain NASA footage"}],
                # The live API offers preview and captions links, never a canonical page link.
                "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/video/N-1/N-1~thumb.jpg"}],
                "href": "https://images-assets.nasa.gov/video/N-1/collection.json",
            }]}},
            # The live manifest lists plain http links.
            ["http://images-assets.nasa.gov/video/N-1/N-1~orig.mp4",
             "http://images-assets.nasa.gov/video/N-1/N-1~medium.mp4",
             "http://images-assets.nasa.gov/video/N-1/N-1~preview.mp4",
             "http://images-assets.nasa.gov/video/N-1/N-1~thumb.jpg"],
        ]
        result = fs._nasa("Apollo launch")
        self.assertEqual(len(result), 1)
        url, metadata = result[0]
        self.assertEqual(url, "https://images-assets.nasa.gov/video/N-1/N-1~orig.mp4")
        self.assertEqual(metadata["alternate_urls"], ["https://images-assets.nasa.gov/video/N-1/N-1~medium.mp4"])
        self.assertEqual(metadata["provider"], "NASA")
        self.assertEqual(metadata["page_url"], "https://images.nasa.gov/details/N-1")

    LOC_HIT = {
        "id": "http://www.loc.gov/item/mp73013300/", "url": "https://www.loc.gov/item/mp73013300/",
        "title": "Empire State Express, the second, taking water on the fly", "date": "1905",
        "access_restricted": False, "contributor": ["porter, edwin s.", "thomas a. edison, inc"],
        "description": ["A locomotive takes water on the fly"],
        "item": {"created_published": ["United States : Thomas A. Edison, Inc., 1905."]},
        "resources": [{"files": 1, "url": "https://www.loc.gov/resource/ntscrm.01875851/",
                       "video": "https://tile.loc.gov/storage-services/service/mbrs/ntscrm/01875851/01875851.mp4",
                       "video_stream": "https://tile.loc.gov/streaming-services/iiif/x/default.m3u8",
                       "width": 320, "height": 240, "duration": 28}],
    }
    LOC_RIGHTS = {"item.rights": [
        "The Library of Congress is not aware of any U.S. copyright or other restrictions in the vast majority "
        "of motion pictures in these collections. Rights assessment is your responsibility. "
        "Credit Line: Library of Congress, Motion Picture, Broadcasting, and Recorded Sound Division."]}

    @patch.object(fs, "_get_json")
    def test_loc_adapter_requires_item_rights_and_video_resource(self, get_json):
        # Search hits carry no rights statement; the item record is fetched for it.
        get_json.side_effect = [{"results": [self.LOC_HIT]}, self.LOC_RIGHTS]
        result = fs._loc("locomotive water")
        self.assertEqual(len(result), 1)
        url, metadata = result[0]
        self.assertTrue(url.endswith("01875851.mp4"))
        self.assertEqual(metadata["provider"], "Library of Congress")
        self.assertEqual(metadata["licence"], "No known copyright restrictions")
        self.assertIn("1905", metadata["rights_basis"])
        self.assertEqual(metadata["licence_state"], "eligible_with_attribution")
        self.assertEqual(get_json.call_args_list[1].args[0], "https://www.loc.gov/item/mp73013300/")

    @patch.object(fs, "_get_json")
    def test_loc_film_without_a_public_domain_basis_is_not_offered(self, get_json):
        # "No known restrictions" is a collection-level opinion, not a licence: a 2012
        # recording has no dated public-domain basis and stays out.
        recent = dict(self.LOC_HIT, date="2012", item={"created_published": ["Washington, D.C., 2012."]})
        get_json.side_effect = [{"results": [recent]}, self.LOC_RIGHTS]
        self.assertEqual(fs._loc("locomotive water"), [])

    @patch.object(fs, "_get_json")
    def test_loc_restricted_or_unstated_rights_are_rejected(self, get_json):
        get_json.side_effect = [{"results": [self.LOC_HIT]},
                                {"item.rights": ["Publication may be restricted. Permission is required."]}]
        self.assertEqual(fs._loc("locomotive water"), [])
        get_json.side_effect = [{"results": [dict(self.LOC_HIT, access_restricted=True)]}]
        self.assertEqual(fs._loc("locomotive water"), [])

    def _commons_page(self, **extmetadata):
        values = {"LicenseShortName": "CC BY-SA 4.0",
                  "LicenseUrl": "https://creativecommons.org/licenses/by-sa/4.0",
                  "Artist": "<a href=\"//commons.wikimedia.org/wiki/User:X\">Example creator</a>",
                  "ImageDescription": "An open film", "ObjectName": "Open film",
                  "AttributionRequired": "true", "Copyrighted": "True", "Restrictions": ""}
        values.update(extmetadata)
        return {"query": {"pages": {"1": {
            "pageid": 1, "title": "File:Open film.webm", "index": 1,
            "videoinfo": [{
                # The live API appends tracking parameters to the file URL.
                "url": "https://upload.wikimedia.org/wikipedia/commons/e/ee/Open_film.webm"
                       "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Open_film.webm",
                "size": 16730451, "width": 768, "height": 576, "duration": 56.5, "mime": "video/webm",
                "derivatives": [{"src": "https://upload.wikimedia.org/wikipedia/commons/transcoded/e/ee/"
                                        "Open_film.webm/Open_film.webm.480p.vp9.webm",
                                 "type": "video/webm; codecs=\"vp9, opus\"", "width": 640, "height": 480}],
                "extmetadata": {key: {"value": value} for key, value in values.items()},
            }],
        }}}}

    @patch.object(fs, "_get_json")
    def test_wikimedia_requires_file_specific_cc_metadata(self, get_json):
        get_json.return_value = self._commons_page()
        result = fs._wikimedia("open film")
        self.assertEqual(len(result), 1)
        url, metadata = result[0]
        self.assertEqual(url, "https://upload.wikimedia.org/wikipedia/commons/e/ee/Open_film.webm")
        self.assertEqual(metadata["licence"], "CC BY-SA 4.0")
        self.assertEqual(metadata["creator"], "Example creator")
        self.assertTrue(metadata["share_alike"])
        self.assertTrue(metadata["alternate_urls"][0].endswith(".480p.vp9.webm"))

    @patch.object(fs, "_get_json")
    def test_wikimedia_rejects_non_commercial_and_unlicensed_files(self, get_json):
        get_json.return_value = self._commons_page(
            LicenseShortName="CC BY-NC 2.0", LicenseUrl="https://creativecommons.org/licenses/by-nc/2.0")
        self.assertEqual(fs._wikimedia("open film"), [])
        get_json.return_value = self._commons_page(LicenseShortName="", LicenseUrl="")
        self.assertEqual(fs._wikimedia("open film"), [])

    @patch.object(fs, "_get_json")
    def test_internet_archive_requires_explicit_item_licence(self, get_json):
        get_json.side_effect = [
            {"response": {"docs": [{"identifier": "open-film", "title": "Open film",
                                    "licenseurl": "http://creativecommons.org/licenses/by/3.0/"}]}},
            {"metadata": {"identifier": "open-film", "title": "Open film", "creator": "A. Maker",
                          "licenseurl": "http://creativecommons.org/licenses/by/3.0/"},
             "files": [{"name": "open-film.avi", "source": "original", "format": "Cinepack", "size": "900000000"},
                       {"name": "sub dir/open film #1.mp4", "source": "derivative", "format": "h.264",
                        "size": "9000000"},
                       {"name": "open-film.ogv", "source": "derivative", "format": "Ogg Video", "size": "5000000"}]},
        ]
        result = fs._internet_archive("open film")
        self.assertEqual(len(result), 1)
        url, metadata = result[0]
        # The licence version comes from the URL (3.0), and the file name is URL-encoded.
        self.assertEqual(metadata["licence"], "CC BY 3.0")
        self.assertEqual(url, "https://archive.org/download/open-film/sub%20dir/open%20film%20%231.mp4")
        self.assertEqual(metadata["creator"], "A. Maker")
        self.assertIn("licenseurl", get_json.call_args_list[0].args[1]["q"])

    @patch.object(fs, "_get_json")
    def test_internet_archive_item_without_a_licence_url_is_rejected(self, get_json):
        get_json.side_effect = [
            {"response": {"docs": [{"identifier": "home-movie", "title": "Home movie"}]}},
            {"metadata": {"identifier": "home-movie", "title": "Home movie"},
             "files": [{"name": "home-movie.mp4", "format": "h.264", "size": "9000000"}]},
        ]
        self.assertEqual(fs._internet_archive("home movie"), [])

    def test_mixkit_manual_import_rejects_restricted_and_records_free(self):
        from tests.footage_fixture import make_clip
        with tempfile.TemporaryDirectory() as tmp:
            source = make_clip(Path(tmp) / "clip.mp4")
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
            self.assertEqual((metadata["width"], metadata["height"]), (640, 360))
            self.assertEqual(len(metadata["media_sha256"]), 64)

    @patch("core.config.NARA_API_KEY", "nara-test")
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
            "page_url": "https://archives.gov/id/1",
            "licence_url": "https://archives.gov/id/1"})]
        with patch.object(fs, "_metadata_relevance", return_value=1.0):
            self.assertTrue(fs.fetch_documentary_clip(["mountain film"], Path("unused.mp4"), set()))
        self.assertTrue(nara.called)
        self.assertTrue(download.called)


if __name__ == "__main__":
    unittest.main()
