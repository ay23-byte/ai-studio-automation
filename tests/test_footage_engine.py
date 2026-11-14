"""Footage engine: validation, licence policy, HTTP behaviour, ranking, and an offline documentary run.

Everything here runs without network access. The provider answers come from
``tests.footage_fake_web`` (response shapes copied from the live APIs) and the
media files are real, made with FFmpeg. These tests prove the code path; they do
not prove any provider is reachable. See scripts/verify_footage_providers.py.
"""
import csv
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from core import footage_report, footage_sources as fs, images
from core.asset_licensing import build_licence_register
from core.footage_engine import Provider, acquire
from core.footage_licence import classify_licence, parse_creative_commons, provenance_gaps
from core.footage_validation import validate_media
from tests.footage_fake_web import LOC_COLLECTION_RIGHTS, FakeResponse, FakeWeb
from tests.footage_fixture import have_ffmpeg, make_clip, truncated_copy

needs_ffmpeg = unittest.skipUnless(have_ffmpeg(), "FFmpeg/FFprobe are required to build and probe real clips")


def _lead(provider, asset_id, title, url, **extra):
    metadata = {"provider": provider, "asset_id": asset_id, "title": title,
                "page_url": f"https://example.org/{asset_id}", "licence": "CC0 1.0",
                "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/"}
    metadata.update(extra)
    return url, metadata


class _Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="footage_test_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        fs.reset_run_state()
        images.reset_stock_state()
        self.report = footage_report.begin_run(self.id())
        self.addCleanup(footage_report.end_run)
        self.addCleanup(fs.reset_run_state)
        env = patch.dict(os.environ, {"FOOTAGE_PACING_FACTOR": "0"})
        env.start()
        self.addCleanup(env.stop)


@needs_ffmpeg
class MediaValidationTests(_Temp):
    def test_real_clip_is_valid_and_measured(self):
        result = validate_media(make_clip(self.tmp / "ok.mp4", audio=True))
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["width"], result["height"]), (640, 360))
        self.assertAlmostEqual(result["duration_seconds"], 4.0, delta=0.3)
        self.assertEqual(result["video_codec"], "h264")
        self.assertTrue(result["has_audio"])
        self.assertEqual(len(result["sha256"]), 64)

    def test_web_page_saved_as_video_is_named_as_such(self):
        page = self.tmp / "page.mp4"
        page.write_bytes(b"<!DOCTYPE html><html>" + b"Access denied " * 6000)
        result = validate_media(page)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_media")

    def test_truncated_transfer_with_a_readable_header_is_rejected(self):
        # ffprobe still reports size and duration for this file; only decoding shows the damage.
        cut = truncated_copy(make_clip(self.tmp / "full.mp4", size="1280x720", seconds=6), self.tmp / "cut.mp4")
        result = validate_media(cut)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "undecodable")
        self.assertIn("truncated", result["reason"])

    def test_each_rejection_has_its_own_status(self):
        (self.tmp / "empty.mp4").write_bytes(b"")
        (self.tmp / "noise.mp4").write_bytes(os.urandom(90_000))
        cases = {
            "missing": self.tmp / "nothing.mp4",
            "empty": self.tmp / "empty.mp4",
            "corrupt": self.tmp / "noise.mp4",
            "low_resolution": make_clip(self.tmp / "small.mp4", size="160x120"),
            "too_short": make_clip(self.tmp / "short.mp4", seconds=1, size="1280x720"),
        }
        for status, path in cases.items():
            with self.subTest(status=status):
                result = validate_media(path)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], status, result["reason"])
                self.assertTrue(result["reason"])


class LicencePolicyTests(unittest.TestCase):
    def state(self, **metadata):
        return classify_licence(metadata)["state"]

    def test_creative_commons_version_is_read_never_assumed(self):
        self.assertEqual(parse_creative_commons("http://creativecommons.org/licenses/by-sa/3.0/")["label"],
                         "CC BY-SA 3.0")
        self.assertEqual(parse_creative_commons("https://creativecommons.org/licenses/by/2.5/")["label"], "CC BY 2.5")
        self.assertEqual(parse_creative_commons("http://creativecommons.org/licenses/publicdomain/")["label"],
                         "Public Domain")
        self.assertEqual(parse_creative_commons("https://creativecommons.org/publicdomain/zero/1.0/")["label"],
                         "CC0 1.0")

    def test_states(self):
        cc = "https://creativecommons.org/licenses/"
        cases = [
            ("eligible", dict(licence="CC0 1.0", licence_url="https://creativecommons.org/publicdomain/zero/1.0/")),
            ("eligible", dict(licence_url="http://creativecommons.org/publicdomain/mark/1.0/")),
            ("eligible", dict(licence_url="http://rightsstatements.org/vocab/NoC-US/1.0/")),
            ("eligible", dict(provider="Pixabay", licence="Pixabay Content License",
                              licence_url="https://pixabay.com/service/license-summary/")),
            ("eligible_with_attribution", dict(licence="CC BY 4.0", licence_url=cc + "by/4.0/")),
            ("eligible_with_attribution", dict(licence="CC BY-SA 3.0", licence_url=cc + "by-sa/3.0/")),
            ("eligible_with_attribution", dict(provider="NASA", licence="NASA Media Usage Guidelines")),
            ("eligible_with_attribution", dict(provider="DVIDS", licence="DVIDS API Commercial Use Terms")),
            ("eligible_with_attribution", dict(provider="Coverr", licence="Coverr License",
                                               licence_url="https://coverr.co/license")),
            ("rejected", dict(licence="CC BY-NC 4.0", licence_url=cc + "by-nc/4.0/")),
            ("rejected", dict(licence="CC BY-ND 4.0", licence_url=cc + "by-nd/4.0/")),
            ("rejected", dict(licence="CC BY-NC-SA 2.0", licence_url=cc + "by-nc-sa/2.0/")),
            ("rejected", dict(provider="Coverr", licence="Coverr License", ai_generated=True)),
            ("restricted", dict(licence_url="http://rightsstatements.org/vocab/InC/1.0/")),
            ("unknown", dict(provider="Internet Archive")),
            ("restricted", dict(licence="All rights reserved")),
            # A permissive URL beside a restrictive or different licence name is a broken record.
            ("restricted", dict(licence="All rights reserved", licence_url=cc + "by/4.0/")),
            ("rejected", dict(licence="CC BY-NC 4.0", licence_url=cc + "by/4.0/")),
            ("unknown", dict(licence="CC BY 4.0", licence_url=cc + "by-sa/4.0/")),
            ("unknown", dict(provider="Wikimedia Commons", licence="NASA Media Usage Guidelines")),
            ("unknown", dict(provider="Library of Congress", licence="No known copyright restrictions",
                             licence_url="https://www.loc.gov/item/x/")),     # no dated basis
            ("unknown", dict(provider="Mixkit", licence="Mixkit Free License")),   # not confirmed by a person
        ]
        for expected, metadata in cases:
            with self.subTest(expected=expected, metadata=metadata):
                self.assertEqual(self.state(**metadata), expected)

    def test_only_two_states_can_be_published(self):
        for metadata, publishable in [
            (dict(licence="CC0 1.0", licence_url="https://creativecommons.org/publicdomain/zero/1.0/"), True),
            (dict(licence="CC BY 4.0", licence_url="https://creativecommons.org/licenses/by/4.0/"), True),
            (dict(licence="CC BY-NC 4.0", licence_url="https://creativecommons.org/licenses/by-nc/4.0/"), False),
            (dict(), False),
        ]:
            self.assertEqual(classify_licence(metadata)["publishable"], publishable)

    def test_permissions_are_recorded_or_left_unknown(self):
        decision = classify_licence({"licence": "CC BY-SA 4.0",
                                     "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/"})
        self.assertEqual((decision["commercial_use"], decision["modification"], decision["attribution_required"],
                          decision["share_alike"]), (True, True, True, True))
        unknown = classify_licence({"provider": "Internet Archive"})
        self.assertIsNone(unknown["commercial_use"])
        self.assertIsNone(unknown["modification"])

    def test_missing_source_page_is_a_provenance_gap(self):
        metadata = {"licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/"}
        self.assertEqual(provenance_gaps(metadata), ["source page URL missing"])
        self.assertEqual(provenance_gaps(dict(metadata, page_url="https://example.org/a")), [])

    def test_named_licence_without_a_url_is_an_incomplete_record_not_an_unknown_licence(self):
        # The release check lets a reviewer supply a missing licence URL, so the licence itself
        # stays recognised; automatic selection is stopped by the gap instead.
        for label, state in (("CC BY 4.0", "eligible_with_attribution"), ("Public Domain", "eligible")):
            metadata = {"licence": label, "page_url": "https://example.org/a"}
            decision = classify_licence(metadata)
            self.assertEqual(decision["state"], state)
            self.assertTrue(any("URL missing" in note for note in decision["notes"]), decision["notes"])
            self.assertEqual(provenance_gaps(metadata), ["licence URL missing"])


class ReleaseCheckAgreementTests(unittest.TestCase):
    """A clip the engine may select must not then be refused by the release check for its licence."""

    SIDECARS = [
        {"provider": "Library of Congress", "licence": "No known copyright restrictions",
         "licence_url": "https://www.loc.gov/item/00694/", "page_url": "https://www.loc.gov/item/00694/",
         "rights_basis": "published in 1903, before the US public-domain cutoff", "attribution_text": "Library of Congress"},
        {"provider": "Mixkit (manual user-selected import)", "licence": "Mixkit Free License", "terms_reviewed": True,
         "licence_url": "https://mixkit.co/license/", "page_url": "https://mixkit.co/free-stock-video/waves-1/",
         "attribution_text": "Mixkit"},
        {"provider": "DVIDS", "licence": "DVIDS API Commercial Use Terms", "attribution_text": "Sgt. A via DVIDS",
         "licence_url": "https://api.dvidshub.net/docs/tos", "page_url": "https://www.dvidshub.net/video/7"},
        {"provider": "Coverr", "licence": "Coverr License", "licence_url": "https://coverr.co/license",
         "page_url": "https://coverr.co/videos/x", "attribution_text": "Video by A on Coverr"},
        {"provider": "Internet Archive", "licence": "Public Domain", "attribution_text": "Internet Archive",
         "licence_url": "http://creativecommons.org/licenses/publicdomain/", "page_url": "https://archive.org/details/x"},
    ]

    def test_selected_sources_carry_their_evidence_into_the_review_manifest(self):
        from core import review_contract, review_packaging
        for sidecar in self.SIDECARS:
            with self.subTest(provider=sidecar["provider"]):
                self.assertTrue(classify_licence(sidecar)["publishable"])
                provenance, rights = review_packaging.describe_asset(sidecar)
                asset = {"asset_id": "a1", "provenance": provenance, "rights": rights}
                self.assertEqual(review_contract.rights_evidence_problems(asset), [])

    def test_evidence_missing_from_the_build_still_blocks_the_release(self):
        from core import review_contract, review_packaging
        for index in (0, 1):
            sidecar = {key: value for key, value in self.SIDECARS[index].items()
                       if key not in ("rights_basis", "terms_reviewed")}
            provenance, rights = review_packaging.describe_asset(sidecar)
            problems = review_contract.rights_evidence_problems(
                {"asset_id": "a1", "provenance": provenance, "rights": rights})
            self.assertTrue(any("unclear or not accepted" in problem for problem in problems), problems)


class ProviderHttpTests(_Temp):
    def response(self, status, payload=None, headers=None):
        return FakeResponse(status, payload=payload, headers=headers,
                            body=b"" if payload is not None else b"error", content_type=None if payload is not None else "text/plain")

    def loc_hit(self, **item):
        record = {"created_published": ["United States : Thomas A. Edison, Inc., 1905."]}
        record.update(item)
        return {"id": "http://www.loc.gov/item/mp73013300/", "url": "https://www.loc.gov/item/mp73013300/",
                "title": item.pop("title", "Empire State Express, the second, taking water on the fly"),
                "description": ["A steam locomotive takes water on the fly."], "date": "1905",
                "access_restricted": False, "contributor": ["thomas a. edison, inc"], "item": record,
                "resources": [{"video": "https://tile.loc.gov/storage-services/service/mbrs/ntscrm/01875851/01875851.mp4",
                               "width": 1920, "height": 1080, "duration": 98}]}

    def loc_search(self, hit, rights):
        def answer(url, params=None, headers=None, timeout=None, **_kwargs):
            if "/item/" in url:
                return self.response(200, {"item.rights": [rights], "item.rights_advisory": {}})
            return self.response(200, {"results": [hit], "pagination": {"next": None}})
        with patch.object(fs.requests, "get", side_effect=answer), patch.object(fs.time, "sleep"), \
                footage_report.provider_scope("Library of Congress", self.report):
            return fs._loc("steam locomotive")

    def test_library_of_congress_collection_rights_text_does_not_refuse_a_dated_film(self):
        # The live text speaks of "copyrighted motion pictures" in "rare cases". That describes
        # the collection; read as a bar on the item it refused every film the Library offers.
        results = self.loc_search(self.loc_hit(), LOC_COLLECTION_RIGHTS)
        self.assertEqual(len(results), 1)
        url, metadata = results[0]
        self.assertTrue(url.endswith("01875851.mp4"))
        self.assertIn("1905", metadata["rights_basis"])
        self.assertEqual(metadata["declared_height"], 1080)
        decision = classify_licence(metadata)
        self.assertTrue(decision["publishable"], decision)
        self.assertEqual(provenance_gaps(metadata, decision), [])
        self.assertEqual(self.report.provider("Library of Congress")["licence_rejections"], 0)

    def test_library_of_congress_item_restriction_is_still_refused_and_named(self):
        text = LOC_COLLECTION_RIGHTS + "\r\nThis film is copyrighted and permission is required for any reuse."
        self.assertEqual(self.loc_search(self.loc_hit(), text), [])
        entry = self.report.provider("Library of Congress")
        self.assertEqual(entry["rejection_reasons"], {"item record states a restriction": 1})
        self.assertEqual(entry["licence_rejections"], 1)

    def test_library_of_congress_home_movie_is_not_public_domain_by_its_date(self):
        # Publication date makes a film public domain; an unpublished home movie has none.
        hit = self.loc_hit(genre=["Home movies"], created_published=["1928."])
        hit["date"] = "1928"
        self.assertEqual(self.loc_search(hit, LOC_COLLECTION_RIGHTS), [])
        self.assertEqual(self.report.provider("Library of Congress")["rejection_reasons"],
                         {"no publication date before the US public-domain cutoff in the catalogue record": 1})

    def test_library_of_congress_spends_no_item_request_on_a_film_it_could_never_use(self):
        # A 2012 event recording has no public-domain date; the Library limits requests strictly.
        hit = self.loc_hit(created_published=["Washington, D.C. : Library of Congress, 2012-02-29."])
        hit["date"] = "2012"
        with patch.object(fs.requests, "get", return_value=self.response(
                200, {"results": [hit], "pagination": {"next": None}})) as get, patch.object(fs.time, "sleep"), \
                footage_report.provider_scope("Library of Congress", self.report):
            self.assertEqual(fs._loc("locomotive"), [])
        self.assertEqual(get.call_count, 1, "only the search; no item record was requested")

    def test_library_of_congress_unreadable_item_record_is_not_called_a_licence_refusal(self):
        def answer(url, params=None, headers=None, timeout=None, **_kwargs):
            if "/item/" in url:
                return self.response(429, headers={"Retry-After": "1200"})
            return self.response(200, {"results": [self.loc_hit(), dict(self.loc_hit(), url="https://www.loc.gov/item/other/")],
                                       "pagination": {"next": None}})
        with patch.object(fs.requests, "get", side_effect=answer) as get, patch.object(fs.time, "sleep"), \
                footage_report.provider_scope("Library of Congress", self.report):
            self.assertEqual(fs._loc("steam locomotive"), [])
        entry = self.report.provider("Library of Congress")
        self.assertEqual(entry["licence_rejections"], 0)
        self.assertEqual(entry["rejection_reasons"], {"item rights record could not be fetched": 1})
        self.assertEqual(entry["search_status"], "rate_limited")
        self.assertEqual(get.call_count, 2, "a provider that asked for a long wait is not asked again")

    def dvids_run(self, accept_legacy):
        seen = []

        def answer(url, params=None, headers=None, timeout=None, **_kwargs):
            seen.append(dict(headers or {}).get("Referer"))
            if dict(headers or {}).get("Referer") == fs.DVIDS_LEGACY_REFERER and accept_legacy:
                return self.response(200, {"results": [], "page_info": {"total_results": 0}})
            return self.response(403)
        with patch.dict(os.environ, {"DVIDS_API_KEY": "key-test", "DVIDS_API_REFERER": ""}), \
                patch.object(fs.requests, "get", side_effect=answer), patch.object(fs.time, "sleep"), \
                footage_report.provider_scope("DVIDS", self.report):
            fs._dvids("army engineers bridge")
            fs._dvids("flood rescue helicopter")
        return seen, self.report.provider("DVIDS")

    def test_dvids_key_bound_to_the_earlier_referer_keeps_working_and_says_so(self):
        seen, entry = self.dvids_run(accept_legacy=True)
        self.assertEqual(seen, [None, fs.DVIDS_LEGACY_REFERER, fs.DVIDS_LEGACY_REFERER])
        self.assertEqual(entry["auth_status"], "configured")
        self.assertEqual(entry["failure_reason"], "")
        self.assertIn("DVIDS_API_REFERER", entry["auth_note"])
        self.assertNotIn("api.dvidshub.net", fs._DISABLED_PROVIDER_HOSTS)
        from scripts.verify_footage_providers import AUTH_FAILED, classify
        self.assertNotEqual(classify(entry)[0], AUTH_FAILED)

    def test_dvids_key_refused_in_both_forms_is_reported_as_a_key_problem(self):
        seen, entry = self.dvids_run(accept_legacy=False)
        self.assertEqual(seen, [None, fs.DVIDS_LEGACY_REFERER], "two forms tried once, then the provider is left alone")
        self.assertEqual(entry["auth_status"], "rejected")
        self.assertIn("both", entry["failure_reason"])
        self.assertIn("api.dvidshub.net", fs._DISABLED_PROVIDER_HOSTS)
        from scripts.verify_footage_providers import AUTH_FAILED, classify
        self.assertEqual(classify(entry)[0], AUTH_FAILED)

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_retry_after_is_honoured(self, get, sleep):
        get.side_effect = [self.response(429, headers={"Retry-After": "7"}), self.response(200, {"ok": True})]
        self.assertEqual(fs._get_json("https://archive.org/advancedsearch.php", {"q": "a"}), {"ok": True})
        self.assertGreaterEqual(sleep.call_args.args[0], 7)

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_long_retry_after_switches_the_provider_off_instead_of_retrying_early(self, get, sleep):
        # The Library of Congress blocks a client for an hour when its limit is passed.
        get.return_value = self.response(429, headers={"Retry-After": "3600"})
        with footage_report.provider_scope("Library of Congress", self.report):
            self.assertIsNone(fs._get_json("https://www.loc.gov/film-and-videos/", {"q": "a"}))
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("www.loc.gov", fs._DISABLED_PROVIDER_HOSTS)
        entry = self.report.provider("Library of Congress")
        self.assertEqual(entry["search_status"], "rate_limited")
        self.assertIn("3600", entry["failure_reason"])

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_server_errors_and_timeouts_are_retried_with_growing_delays(self, get, sleep):
        get.side_effect = [self.response(503), requests.ConnectTimeout("slow"), self.response(200, {"ok": 1})]
        self.assertEqual(fs._get_json("https://archive.org/metadata/x"), {"ok": 1})
        delays = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(len(delays), 2)
        self.assertGreater(delays[1], delays[0] * 0.9)

    @patch.object(fs.requests, "get")
    def test_not_found_skips_one_request_without_disabling_the_provider(self, get):
        get.return_value = self.response(404)
        self.assertIsNone(fs._get_json("https://archive.org/metadata/missing"))
        self.assertNotIn("archive.org", fs._DISABLED_PROVIDER_HOSTS)

    @patch.object(fs.requests, "get")
    def test_auth_failure_is_reported_with_the_documented_cause_and_no_key(self, get):
        get.return_value = self.response(403)
        with footage_report.provider_scope("DVIDS", self.report):
            fs._get_json("https://api.dvidshub.net/search", {"api_key": "key-SECRET123456", "q": "a"})
        entry = self.report.provider("DVIDS")
        self.assertEqual(entry["auth_status"], "rejected")
        self.assertEqual(entry["http_status"], 403)
        self.assertIn("origin", entry["failure_reason"])
        self.assertNotIn("SECRET", json.dumps(self.report.to_dict()))

    @patch.object(fs.time, "sleep")
    @patch.object(fs.requests, "get")
    def test_keys_in_urls_never_reach_the_report(self, get, _sleep):
        get.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool(host='api.dvidshub.net'): Max retries exceeded with url: "
            "/search?api_key=key-SECRET123456&q=a")
        with footage_report.provider_scope("DVIDS", self.report):
            fs._get_json("https://api.dvidshub.net/search", {"api_key": "key-SECRET123456", "q": "a"})
        dumped = json.dumps(self.report.to_dict())
        self.assertNotIn("SECRET123456", dumped)
        self.assertEqual(self.report.provider("DVIDS")["search_status"], "network_error")
        self.assertEqual(footage_report.redact("https://x/?wskey=abc123&key=zzz&q=1"),
                         "https://x/?wskey=REDACTED&key=REDACTED&q=1")

    @patch.object(fs.requests, "get")
    def test_captcha_page_is_reported_as_a_block(self, get):
        get.return_value = FakeResponse(200, body=b"<html>Please complete the CAPTCHA</html>", content_type="text/html")
        with footage_report.provider_scope("Library of Congress", self.report):
            self.assertIsNone(fs._get_json("https://www.loc.gov/film-and-videos/", {"q": "a"}))
        self.assertEqual(self.report.provider("Library of Congress")["search_status"], "blocked")
        self.assertIn("www.loc.gov", fs._DISABLED_PROVIDER_HOSTS)

    @patch.object(fs.requests, "get")
    def test_repeated_request_is_answered_from_the_run_cache(self, get):
        get.return_value = self.response(200, {"n": 1})
        for _ in range(3):
            self.assertEqual(fs._get_json("https://archive.org/advancedsearch.php", {"q": "same"}), {"n": 1})
        self.assertEqual(get.call_count, 1)

    def test_library_of_congress_is_paced_below_its_published_limit(self):
        # 20 requests a minute is one every 3 seconds.
        self.assertGreaterEqual(fs.HOST_MIN_INTERVAL["www.loc.gov"], 3.0)

    @patch.object(images.requests, "get")
    def test_pixabay_auth_failure_stops_later_requests(self, get):
        images.PIXABAY_API_KEY = "pix-test"
        self.addCleanup(setattr, images, "PIXABAY_API_KEY", "")
        get.return_value = Mock(status_code=403, headers={})
        self.assertEqual(images._search_pixabay("first"), [])
        self.assertEqual(images._search_pixabay("second"), [])
        get.assert_called_once()

    @patch.object(images.time, "sleep")
    @patch.object(images.requests, "get")
    def test_pixabay_rate_limit_waits_for_the_reset_the_api_reports(self, get, sleep):
        images.PIXABAY_API_KEY = "pix-test"
        self.addCleanup(setattr, images, "PIXABAY_API_KEY", "")
        get.side_effect = [Mock(status_code=429, headers={"X-RateLimit-Reset": "4"}),
                           Mock(status_code=200, headers={}, json=lambda: {"hits": [{"id": 1}]})]
        self.assertEqual(images._search_pixabay("river"), [{"id": 1}])
        self.assertGreaterEqual(sleep.call_args.args[0], 4)

    def test_pixabay_candidate_prefers_full_hd_over_4k_and_skips_empty_renditions(self):
        hit = {"id": 5, "tags": "river, mountain", "duration": 20, "user": "u", "pageURL": "https://pixabay.com/videos/id-5/",
               "videos": {"large": {"url": "https://cdn.pixabay.com/5_large.mp4", "width": 3840, "height": 2160},
                          "medium": {"url": "https://cdn.pixabay.com/5_medium.mp4", "width": 1920, "height": 1080},
                          "small": {"url": "", "width": 1280, "height": 720},
                          "tiny": {"url": "https://cdn.pixabay.com/5_tiny.mp4", "width": 640, "height": 360}}}
        url, metadata = images._pixabay_candidate(hit, "mountain river")
        self.assertEqual(url, "https://cdn.pixabay.com/5_medium.mp4")
        self.assertEqual(metadata["alternate_urls"], ["https://cdn.pixabay.com/5_large.mp4"])
        self.assertIsNone(images._pixabay_candidate(dict(hit, duration=2), "mountain river"))

    def test_coverr_candidate_reads_live_fields_and_flags_ai_content(self):
        hit = {"id": "abc", "title": "Mountain river", "description": "A river in the mountains",
               "tags": ["river"], "duration": 9.5, "max_width": 1920, "max_height": 1080, "is_ai_generated": True,
               "urls": {"mp4": "https://cdn.coverr.co/abc.mp4", "mp4_download": "https://cdn.coverr.co/abc_dl.mp4"}}
        url, metadata = images._coverr_candidate(hit, "mountain river")
        self.assertEqual(url, "https://cdn.coverr.co/abc_dl.mp4")
        self.assertEqual((metadata["declared_width"], metadata["declared_height"]), (1920, 1080))
        self.assertEqual(classify_licence(metadata)["state"], "rejected")
        self.assertEqual(classify_licence(dict(metadata, ai_generated=False))["state"], "eligible_with_attribution")


@needs_ffmpeg
class EngineTests(_Temp):
    def clip_bytes(self, tag):
        return make_clip(self.tmp / f"src_{tag}.mp4", tag=tag).read_bytes()

    def downloader(self, files):
        """A download function serving ``files[url]`` and validating like production."""
        def download(url, path, metadata):
            data = files.get(url)
            if data is None:
                metadata["validation"] = {"ok": False, "status": "download_failed", "reason": "HTTP 404"}
                return False
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(data)
            result = validate_media(path)
            if not result["ok"]:
                Path(path).unlink()
                metadata["validation"] = result
                return False
            metadata.update(width=result["width"], height=result["height"],
                            duration_seconds=result["duration_seconds"], media_sha256=result["sha256"],
                            validation={"ok": True, "status": "valid", "reason": result["reason"]})
            return True
        return download

    def run_scene(self, providers, files, queries=("steam train",), used=None, name="scene.mp4", prompt=""):
        used = used if used is not None else set()
        ok = acquire(list(queries), self.tmp / name, used, scene_prompt=prompt, providers=providers,
                     download=self.downloader(files), relevance=fs._subject_relevance, report=self.report,
                     scene_label=name)
        return ok, used

    def test_providers_are_searched_at_the_same_time(self):
        def slow(name):
            def search(_query):
                time.sleep(0.4)
                return []
            return Provider(name, search)
        started = time.monotonic()
        self.run_scene([slow(f"p{i}") for i in range(5)], {})
        self.assertLess(time.monotonic() - started, 1.5)   # five in sequence would take 2 seconds

    def test_one_failing_provider_does_not_cost_the_scene(self):
        def broken(_query):
            raise RuntimeError("HTTP 500 from catalogue")
        files = {"https://a/1.mp4": self.clip_bytes("a")}
        ok, _ = self.run_scene([Provider("Broken", broken),
                                Provider("Working", lambda q: [_lead("Working", "1", "Steam train", "https://a/1.mp4")])],
                               files)
        self.assertTrue(ok)
        self.assertEqual(self.report.provider("Broken")["search_status"], "error")
        self.assertIn("HTTP 500", self.report.provider("Broken")["failure_reason"])
        self.assertEqual(self.report.provider("Working")["selected_scenes"], 1)

    def test_provider_that_outlives_its_budget_is_left_behind(self):
        def hangs(_query):
            time.sleep(3)
            return [_lead("Slow", "s", "Steam train", "https://s/1.mp4")]
        files = {"https://a/1.mp4": self.clip_bytes("a")}
        with patch.dict(os.environ, {"FOOTAGE_PROVIDER_SEARCH_SECONDS": "0.3", "FOOTAGE_SEARCH_GRACE_SECONDS": "0.3"}):
            started = time.monotonic()
            ok, _ = self.run_scene([Provider("Slow", hangs),
                                    Provider("Fast", lambda q: [_lead("Fast", "1", "Steam train", "https://a/1.mp4")])],
                                   files)
        self.assertTrue(ok)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(self.report.provider("Slow")["search_status"], "timeout")

    def test_unconfigured_provider_is_reported_not_called(self):
        search = Mock(return_value=[])
        provider = Provider("Keyed", search, status=lambda: (False, "missing_key", "KEYED_API_KEY is not set"))
        ok, _ = self.run_scene([provider], {})
        self.assertFalse(ok)
        search.assert_not_called()
        entry = self.report.provider("Keyed")
        self.assertEqual((entry["enabled"], entry["auth_status"]), (False, "missing_key"))
        self.assertIn("KEYED_API_KEY", entry["disabled_reason"])

    def test_unlicensed_and_irrelevant_results_are_never_downloaded(self):
        download = Mock(return_value=True)
        leads = [
            _lead("P", "nc", "Steam train", "https://a/nc.mp4", licence="CC BY-NC 4.0",
                  licence_url="https://creativecommons.org/licenses/by-nc/4.0/"),
            _lead("P", "none", "Steam train", "https://a/none.mp4", licence="", licence_url=""),
            _lead("P", "nopage", "Steam train", "https://a/nopage.mp4", page_url="", licence="CC BY 4.0",
                  licence_url="https://creativecommons.org/licenses/by/4.0/"),
            _lead("P", "beach", "Sunset on a tropical beach", "https://a/beach.mp4"),
        ]
        ok = acquire(["steam train"], self.tmp / "s.mp4", set(), providers=[Provider("P", lambda q: leads)],
                     download=download, relevance=fs._subject_relevance, report=self.report)
        self.assertFalse(ok)
        download.assert_not_called()
        scene = self.report.to_dict()["scenes"][0]
        self.assertEqual(scene["status"], "needs_manual_review")
        self.assertIn("licence rejected", scene["reason"])
        self.assertIn("licence unknown", scene["reason"])
        self.assertIn("source page URL missing", scene["reason"])
        self.assertIn("not relevant enough", scene["reason"])
        self.assertEqual(self.report.provider("P")["licence_rejections"], 3)

    def test_invalid_file_is_skipped_and_the_next_candidate_is_used(self):
        files = {"https://a/page.mp4": b"<!DOCTYPE html>" + b"denied " * 20000,
                 "https://b/good.mp4": self.clip_bytes("good")}
        ok, _ = self.run_scene(
            [Provider("A", lambda q: [_lead("A", "1", "Steam train engine", "https://a/page.mp4", declared_height=1080,
                                            declared_width=1920)]),
             Provider("B", lambda q: [_lead("B", "2", "Steam train", "https://b/good.mp4")])], files)
        self.assertTrue(ok)
        self.assertEqual(self.report.provider("A")["invalid_media"], 1)
        self.assertEqual(self.report.provider("A")["download_successes"], 0)
        self.assertEqual(self.report.provider("B")["eligible_assets"], 1)
        scene = self.report.to_dict()["scenes"][0]
        self.assertEqual(scene["selected"]["provider"], "B")
        self.assertEqual(scene["rejected_before_selection"][0]["outcome"], "not_media")

    def test_same_file_from_two_providers_is_one_candidate(self):
        download = Mock(return_value=False)
        acquire(["steam train"], self.tmp / "s.mp4", set(), providers=[
            Provider("A", lambda q: [_lead("A", "1", "Steam train", "https://cdn.example/film.mp4?utm_source=a")]),
            Provider("B", lambda q: [_lead("B", "9", "Steam train", "https://cdn.example/film.mp4")]),
        ], download=download, relevance=fs._subject_relevance, report=self.report)
        self.assertEqual(download.call_count, 1)

    def test_identical_content_is_not_used_twice_in_one_documentary(self):
        same = self.clip_bytes("same")
        files = {"https://a/1.mp4": same, "https://b/copy.mp4": same, "https://b/other.mp4": self.clip_bytes("other")}
        used = set()
        ok, used = self.run_scene([Provider("A", lambda q: [_lead("A", "1", "Steam train", "https://a/1.mp4")])],
                                  files, used=used, name="one.mp4")
        self.assertTrue(ok)
        # Scene two: provider B lists the same film under another id, and one different film.
        provider_b = Provider("B", lambda q: [
            _lead("B", "copy", "Steam train", "https://b/copy.mp4", declared_width=1920, declared_height=1080),
            _lead("B", "other", "Steam train", "https://b/other.mp4")])
        ok, used = self.run_scene([provider_b], files, used=used, name="two.mp4")
        self.assertTrue(ok)
        scenes = self.report.to_dict()["scenes"]
        self.assertEqual(scenes[1]["selected"]["asset_id"], "other")
        self.assertEqual(scenes[1]["rejected_before_selection"][0]["outcome"], "duplicate_content")
        self.assertNotEqual(scenes[0]["selected"]["media_sha256"], scenes[1]["selected"]["media_sha256"])
        # The same catalogue entry is not offered again either.
        ok, _ = self.run_scene([Provider("A", lambda q: [_lead("A", "1", "Steam train", "https://a/1.mp4")])],
                               files, used=used, name="three.mp4")
        self.assertFalse(ok)

    def test_equally_relevant_providers_share_the_documentary(self):
        names = ["Alpha", "Beta", "Gamma"]
        files, providers = {}, []
        for name in names:
            leads = []
            for index in range(6):
                url = f"https://{name.lower()}/{index}.mp4"
                files[url] = self.clip_bytes(f"{name}{index}")
                leads.append(_lead(name, str(index), "Steam train", url))
            providers.append(Provider(name, lambda q, leads=leads: leads))
        used = set()
        for scene in range(6):
            ok, used = self.run_scene(providers, files, used=used, name=f"scene{scene}.mp4")
            self.assertTrue(ok)
        share = self.report.to_dict()["summary"]["selected_by_provider"]
        self.assertEqual(share, {"Alpha": 2, "Beta": 2, "Gamma": 2})

    def test_diversity_never_beats_relevance(self):
        files = {"https://a/1.mp4": self.clip_bytes("a1"), "https://a/2.mp4": self.clip_bytes("a2"),
                 "https://b/1.mp4": self.clip_bytes("b1")}
        used = {"A:x", "A:y", "A:z"}            # A already supplied three scenes
        providers = [
            Provider("A", lambda q: [_lead("A", "1", "Steam train crossing a bridge", "https://a/1.mp4")]),
            Provider("B", lambda q: [_lead("B", "1", "Train station", "https://b/1.mp4")]),
        ]
        ok, _ = self.run_scene(providers, files, queries=("steam train bridge",), used=used)
        self.assertTrue(ok)
        self.assertEqual(self.report.to_dict()["scenes"][0]["selected"]["provider"], "A")

    def test_scene_description_orders_results_that_match_the_same_words(self):
        files = {"https://a/1.mp4": self.clip_bytes("1"), "https://a/2.mp4": self.clip_bytes("2")}
        leads = [_lead("A", "1", "Steam train", "https://a/1.mp4", description="A toy model on a table"),
                 _lead("A", "2", "Steam train", "https://a/2.mp4", description="Crossing a mountain viaduct in snow")]
        ok, _ = self.run_scene([Provider("A", lambda q: leads)], files,
                               prompt="A steam train crosses a snowy mountain viaduct")
        self.assertTrue(ok)
        self.assertEqual(self.report.to_dict()["scenes"][0]["selected"]["asset_id"], "2")

    def test_report_files_are_written_and_readable(self):
        files = {"https://a/1.mp4": self.clip_bytes("a")}
        self.run_scene([Provider("A", lambda q: [_lead("A", "1", "Steam train", "https://a/1.mp4")])], files)
        self.run_scene([Provider("A", lambda q: [])], files, queries=("a walrus playing chess",), name="none.mp4")
        written = self.report.write(self.tmp / "build")
        data = json.loads(Path(written["json_path"]).read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["scenes_with_footage"], 1)
        self.assertEqual(data["summary"]["scenes_needing_manual_review"], 1)
        self.assertEqual(data["manual_review_scenes"][0]["scene"], "none.mp4")
        entry = data["providers"][0]
        for key in ("enabled", "auth_status", "search_status", "http_status", "results_found",
                    "downloadable_candidates", "download_attempts", "download_successes", "invalid_media",
                    "licence_rejections", "eligible_assets", "selected_scenes", "failure_reason"):
            self.assertIn(key, entry)
        text = Path(written["markdown_path"]).read_text(encoding="utf-8")
        self.assertIn("Scenes that need a person", text)
        self.assertIn("| A |", text)


@needs_ffmpeg
class DownloadTests(_Temp):
    def test_corrupt_first_rendition_falls_back_to_the_next_one(self):
        web = FakeWeb(self.tmp)
        web.clip("https://cdn.example/orig.mp4", kind="truncated", size="1280x720", seconds=6)
        web.clip("https://cdn.example/medium.mp4")
        metadata = {"provider": "NASA", "asset_id": "N-1", "title": "Launch", "relevance_score": 0.8,
                    "licence": "NASA Media Usage Guidelines", "licence_url": fs.NASA_LICENCE_URL,
                    "page_url": "https://images.nasa.gov/details/N-1",
                    "alternate_urls": ["https://cdn.example/medium.mp4"]}
        target = self.tmp / "out.mp4"
        with patch("requests.get", web.get):
            self.assertTrue(fs._download("http://cdn.example/orig.mp4", target, metadata))
        sidecar = json.loads(target.with_suffix(".mp4.source.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["download_url"], "https://cdn.example/medium.mp4")
        self.assertEqual(sidecar["rendition_index"], 1)
        self.assertTrue(sidecar["validation"]["ok"])
        self.assertEqual(len(sidecar["media_sha256"]), 64)
        for key in ("width", "height", "duration_seconds", "file_size_bytes", "download_timestamp", "local_path"):
            self.assertTrue(sidecar[key], key)
        self.assertNotIn("alternate_urls", sidecar)

    def test_rejected_download_leaves_no_file_and_says_why(self):
        web = FakeWeb(self.tmp)
        web.clip("https://cdn.example/page.mp4", kind="html")
        metadata = {"provider": "Internet Archive", "asset_id": "x", "relevance_score": 0.9}
        target = self.tmp / "out.mp4"
        with patch("requests.get", web.get):
            self.assertFalse(fs._download("https://cdn.example/page.mp4", target, metadata))
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".mp4.source.json").exists())
        self.assertEqual(metadata["validation"]["status"], "not_media")

    def test_oversized_file_is_refused_from_its_declared_length(self):
        web = FakeWeb(self.tmp)
        web.clip("https://cdn.example/big.mp4", size="1280x720", seconds=12)      # about 2 MB
        metadata = {"provider": "Internet Archive", "asset_id": "x", "relevance_score": 0.9}
        with patch("requests.get", web.get), patch.dict(os.environ, {"FOOTAGE_MAX_CLIP_MB": "1"}):
            self.assertFalse(fs._download("https://cdn.example/big.mp4", self.tmp / "o.mp4", metadata))
        self.assertEqual(metadata["validation"]["status"], "download_failed")
        self.assertIn("MB limit", metadata["validation"]["reason"])


@needs_ffmpeg
class SyntheticDocumentaryTests(_Temp):
    """Eight scenes through the production entry point against the offline provider stand-ins."""

    SCENES = [
        ("launch", ["rocket launch", "Saturn rocket"], "A Saturn rocket launch from the pad"),
        ("locomotive", ["steam locomotive"], "A steam locomotive leaves the station"),
        ("harbour", ["harbour ships"], "Ships in a busy harbour"),
        ("street", ["street scene"], "A street scene in the early 1900s"),
        ("helicopter", ["helicopter flight"], "A military helicopter flight over hills"),
        ("river", ["mountain river"], "A mountain river in spring"),
        ("traffic", ["city traffic"], "Modern city traffic at night"),
        ("walrus", ["walrus playing chess"], "A walrus playing chess underwater"),
    ]

    def build_web(self):
        web = FakeWeb(self.tmp / "web")
        web.add_nasa("KSC-69", "Saturn rocket launch", "Apollo Saturn rocket launch from the pad", size="1280x720")
        web.add_nasa("KSC-70", "Rocket launch (agency photo)", "Rocket launch. Copyright Getty Images.")
        web.add_wikimedia(11, "Steam locomotive leaving station", "A steam locomotive leaves the station")
        web.add_wikimedia(12, "Steam locomotive NC", "A steam locomotive (non-commercial)", licence="CC BY-NC 2.0",
                          licence_url="https://creativecommons.org/licenses/by-nc/2.0")
        web.add_europeana("harbour1", "Harbour ships newsreel", "Ships in a busy harbour")
        web.add_europeana("harbour2", "Harbour ships (in copyright)", "Ships in a harbour",
                          rights="http://rightsstatements.org/vocab/InC/1.0/")
        web.add_loc("00694", "Street scene", "A street scene filmed in 1903", year="1903")
        web.add_loc("00999", "Street scene lecture", "A talk about a street scene", year="2012")
        web.add_dvids(77, "Helicopter flight training", "A military helicopter flight over hills")
        web.add_internet_archive("river-film", "Mountain river", "A mountain river in spring")
        web.add_internet_archive("river-nolicence", "Mountain river home movie", "A mountain river", licenseurl="")
        web.add_pixabay(501, "city, traffic, night")
        web.add_coverr("cov1", "City traffic timelapse", "City traffic at night", tags=["city", "traffic"])
        web.add_coverr("cov2", "City traffic (AI)", "City traffic at night", tags=["city", "traffic"], ai=True)
        return web

    def run_documentary(self, web, scenes=None):
        used, shots = set(), []
        keys = {"DVIDS_API_KEY": "key-dvids-test", "FOOTAGE_PACING_FACTOR": "0"}
        with patch("requests.get", web.get), patch("requests.patch", web.patch), patch.dict(os.environ, keys), \
                patch("core.config.EUROPEANA_API_KEY", "eu-test"), patch.object(images, "PIXABAY_API_KEY", "pix-test"), \
                patch.object(images, "COVERR_API_KEY", "cov-test"), patch.object(fs.time, "sleep"), \
                patch.object(images.time, "sleep"):
            for index, (label, keywords, prompt) in enumerate(scenes or self.SCENES):
                clip = self.tmp / "build" / f"{label}.mp4"
                if images.fetch_clip(keywords, clip, used, scene_prompt=prompt):
                    shots.append({"_start": index * 120.0, "_media": {"type": "video", "path": str(clip)}})
        return used, shots

    def test_documentary_uses_many_providers_and_records_everything(self):
        from core import review_contract, review_packaging
        web = self.build_web()
        used, shots = self.run_documentary(web)
        data = self.report.to_dict()
        chosen = {scene["scene"]: (scene.get("selected") or {}).get("provider") for scene in data["scenes"]}
        self.assertEqual(chosen["launch"], "NASA")
        self.assertEqual(chosen["locomotive"], "Wikimedia Commons")
        self.assertEqual(chosen["harbour"], "Europeana")
        self.assertEqual(chosen["street"], "Library of Congress")
        self.assertEqual(chosen["helicopter"], "DVIDS")
        self.assertEqual(chosen["river"], "Internet Archive")
        self.assertIn(chosen["traffic"], {"Pixabay", "Coverr"})
        self.assertIsNone(chosen["walrus"])
        self.assertGreaterEqual(len(data["summary"]["selected_by_provider"]), 7)
        self.assertEqual(data["summary"]["scenes_needing_manual_review"], 1)
        self.assertEqual(data["manual_review_scenes"][0]["scene"], "walrus")

        # Every selected file is real, validated footage with a complete sidecar.
        hashes = set()
        for shot in shots:
            clip = Path(shot["_media"]["path"])
            self.assertTrue(validate_media(clip)["ok"])
            sidecar = json.loads(clip.with_suffix(".mp4.source.json").read_text(encoding="utf-8"))
            for key in ("provider", "asset_id", "title", "page_url", "download_url", "licence", "licence_url",
                        "licence_state", "attribution_text", "width", "height", "duration_seconds",
                        "media_sha256", "download_timestamp", "relevance_score"):
                self.assertTrue(sidecar.get(key), f"{sidecar.get('provider')}: {key}")
            self.assertIn(sidecar["licence_state"], ("eligible", "eligible_with_attribution"))
            self.assertTrue(sidecar["validation"]["ok"])
            hashes.add(sidecar["media_sha256"])
            # The release check, which sees only the review manifest, accepts the same licence.
            provenance, rights = review_packaging.describe_asset(sidecar)
            self.assertEqual(provenance["origin"], "source", sidecar["provider"])
            self.assertEqual(review_contract.rights_evidence_problems(
                {"asset_id": clip.stem, "provenance": provenance, "rights": rights}), [], sidecar["provider"])
            self.assertTrue(rights["attribution"], sidecar["provider"])
        self.assertEqual(len(hashes), len(shots))

        # Rejections were counted against the provider that offered them.
        by_name = {entry["provider"]: entry for entry in data["providers"]}
        for name in ("NASA", "Wikimedia Commons", "Europeana", "Library of Congress", "Internet Archive"):
            self.assertGreaterEqual(by_name[name]["licence_rejections"], 1, name)
            self.assertEqual(by_name[name]["download_successes"], 1, name)
        self.assertEqual(by_name["National Archives (NARA)"]["auth_status"], "missing_key")
        self.assertFalse(by_name["National Archives (NARA)"]["enabled"])
        # The licensed-only Internet Archive search never returned the unlicensed item's file.
        self.assertNotIn("home movie", json.dumps(data["scenes"]).lower())

        # Documented authentication was used; keys never appear in the report.
        europeana = [call for call in web.calls if call[0] == "api.europeana.eu"][0]
        self.assertEqual(europeana[3].get("X-Api-Key"), "eu-test")
        self.assertNotIn("wskey", europeana[2])
        coverr = [call for call in web.calls if call[0] == "api.coverr.co"][0]
        self.assertEqual(coverr[3].get("Authorization"), "Bearer cov-test")
        dumped = json.dumps(data)
        for secret in ("key-dvids-test", "eu-test", "pix-test", "cov-test"):
            self.assertNotIn(secret, dumped)

        # licenses.csv and credits.txt describe exactly what was used.
        register = build_licence_register(shots, self.tmp / "build")
        self.assertEqual(register["blocked_count"], 0)
        self.assertEqual(register["eligible_pending_human_review"], len(shots))
        with open(register["csv_path"], newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["Wikimedia Commons"]["licence"], "CC BY-SA 4.0")
        self.assertEqual(by_source["Wikimedia Commons"]["share_alike"], "yes")
        self.assertEqual(by_source["Wikimedia Commons"]["attribution_required"], "yes")
        self.assertEqual(by_source["Europeana"]["licence_state"], "eligible")
        self.assertEqual(by_source["Europeana"]["attribution_required"], "no")
        self.assertEqual(by_source["DVIDS"]["commercial_use_allowed"], "yes")
        for row in rows:
            self.assertEqual(row["use_status"], "eligible_pending_human_review")
            self.assertEqual(row["file_validated"], "yes")
            self.assertEqual(len(row["media_sha256"]), 64)
            self.assertTrue(row["url"].startswith("https://"))
            self.assertTrue(row["download_url"].startswith("https://"))
            self.assertEqual(row["human_review_required"], "True")
        credits = Path(register["credits_path"]).read_text(encoding="utf-8")
        self.assertIn("Steam locomotive leaving station", credits)
        self.assertIn("CC BY-SA 4.0", credits)
        self.assertIn("Commons User", credits)
        self.assertIn("DVIDS", credits)
        self.assertIn("NASA", credits)
        self.assertFalse(register["publication_ready"])

    def test_refused_and_rate_limited_providers_are_named_while_the_rest_carry_on(self):
        web = self.build_web()
        web.failures["api.dvidshub.net"] = 403
        web.failures["www.loc.gov"] = 429
        web.failure_headers["www.loc.gov"] = {"Retry-After": "3600"}
        web.failures["api.europeana.eu"] = 401
        web.failures["pixabay.com"] = requests.ConnectTimeout("timed out")
        self.run_documentary(web)
        data = self.report.to_dict()
        by_name = {entry["provider"]: entry for entry in data["providers"]}
        self.assertEqual(by_name["DVIDS"]["auth_status"], "rejected")
        self.assertEqual(by_name["DVIDS"]["http_status"], 403)
        self.assertIn("origin", by_name["DVIDS"]["failure_reason"])
        self.assertEqual(by_name["Europeana"]["auth_status"], "rejected")
        self.assertIn("EUROPEANA_API_KEY", by_name["Europeana"]["failure_reason"])
        self.assertEqual(by_name["Library of Congress"]["search_status"], "rate_limited")
        self.assertEqual(by_name["Pixabay"]["search_status"], "network_error")
        self.assertIn("unreachable", by_name["Pixabay"]["disabled_reason"])
        # An unreachable provider is tried for two searches (three attempts each), then left alone.
        self.assertEqual(len([call for call in web.calls if call[0] == "pixabay.com"]), 6)
        # Each refused provider was asked once, not once per scene. DVIDS is asked twice in
        # total: once with no Referer and once with the earlier fixed Referer.
        for host, expected in (("api.dvidshub.net", 2), ("www.loc.gov", 1), ("api.europeana.eu", 1)):
            self.assertEqual(len([call for call in web.calls if call[0] == host]), expected, host)
        chosen = {scene["scene"]: (scene.get("selected") or {}).get("provider") for scene in data["scenes"]}
        self.assertEqual(chosen["launch"], "NASA")
        self.assertEqual(chosen["locomotive"], "Wikimedia Commons")
        self.assertEqual(chosen["river"], "Internet Archive")
        self.assertEqual(chosen["traffic"], "Coverr")
        for scene in ("harbour", "street", "helicopter", "walrus"):
            self.assertIsNone(chosen[scene], scene)
        self.assertEqual(data["summary"]["scenes_needing_manual_review"], 4)

    def test_archival_scene_never_receives_stock_footage(self):
        web = self.build_web()
        self.run_documentary(web, scenes=[
            ("archive", ["night traffic"], "genuine archival footage of night traffic")])
        self.assertNotIn("pixabay.com", web.hosts_called())
        self.assertNotIn("api.coverr.co", web.hosts_called())
        scene = self.report.to_dict()["scenes"][0]
        self.assertEqual(scene["status"], "needs_manual_review")

    def test_a_shared_generic_word_is_not_a_match(self):
        # The expansion adds "newsreel" to archival queries; that word alone must not select a clip.
        harbour = {"title": "Harbour ships newsreel", "description": "Ships in a busy harbour"}
        self.assertEqual(fs._subject_relevance("night traffic newsreel", harbour), 0.0)
        self.assertGreater(fs._subject_relevance("harbour newsreel", harbour), 0.9)
        self.assertEqual(fs._subject_relevance("newsreel", harbour), 1.0)      # nothing but the generic word asked for


@needs_ffmpeg
class MixkitImportTests(_Temp):
    def test_manifest_import_validates_each_clip_and_reports_rejections(self):
        make_clip(self.tmp / "in" / "good.mp4", size="1280x720")
        (self.tmp / "in" / "page.mp4").write_bytes(b"<!DOCTYPE html>" + b"x" * 80_000)
        make_clip(self.tmp / "in" / "restricted.mp4", tag="r")
        manifest = self.tmp / "in" / "manifest.json"
        manifest.write_text(json.dumps([
            {"file": "good.mp4", "title": "Waves on a beach", "licence": "Mixkit Free License",
             "source_url": "https://mixkit.co/free-stock-video/waves-on-a-beach-1234/", "tags": "waves, beach"},
            {"file": "page.mp4", "title": "Broken", "licence": "Mixkit Free License",
             "source_url": "https://mixkit.co/free-stock-video/broken-1/"},
            {"file": "restricted.mp4", "title": "Restricted", "licence": "Mixkit Restricted License",
             "source_url": "https://mixkit.co/free-stock-video/restricted-2/"},
            {"file": "missing.mp4", "title": "Missing", "licence": "Mixkit Free License",
             "source_url": "https://mixkit.co/free-stock-video/missing-3/"},
            {"file": "good.mp4", "title": "Wrong site", "licence": "Mixkit Free License",
             "source_url": "https://example.com/free-stock-video/x/"},
        ]), encoding="utf-8")
        old = Path.cwd()
        os.chdir(self.tmp)
        try:
            refused = fs.import_mixkit_manifest(manifest)
            self.assertEqual(len(refused["imported"]), 0)       # licence not confirmed by the operator
            result = fs.import_mixkit_manifest(manifest, confirm_free_license=True)
            self.assertEqual([item["file"] for item in result["imported"]], ["good.mp4"])
            reasons = {item["entry"]: item["reason"] for item in result["rejected"]}
            self.assertIn("not_media", reasons[2])
            self.assertIn("Restricted", reasons[3])
            self.assertIn("missing.mp4", reasons[4])
            self.assertIn("Mixkit asset page", reasons[5])
            sidecar = json.loads(Path(result["imported"][0]["sidecar"]).read_text(encoding="utf-8"))
            self.assertEqual((sidecar["width"], sidecar["height"]), (1280, 720))
            self.assertEqual(sidecar["tags"], ["waves", "beach"])
            self.assertEqual(classify_licence(sidecar)["state"], "eligible")
            # The imported clip is found for a matching scene and counted in the report.
            target = self.tmp / "build" / "scene.mp4"
            self.assertTrue(fs.fetch_documentary_clip(["waves beach"], target, set(), scene_prompt="Waves on a beach"))
            self.assertTrue(target.is_file())
        finally:
            os.chdir(old)
        scene = self.report.to_dict()["scenes"][0]
        self.assertEqual(scene["selected"]["provider"], "Mixkit (manual user-selected import)")


    def test_command_line_imports_a_manifest_and_refuses_without_confirmation(self):
        import contextlib
        import io
        from scripts import register_mixkit_asset as cli
        make_clip(self.tmp / "in" / "good.mp4")
        manifest = self.tmp / "in" / "manifest.json"
        manifest.write_text(json.dumps([{
            "file": "good.mp4", "title": "City lights", "licence": "Mixkit Free License",
            "source_url": "https://mixkit.co/free-stock-video/city-lights-77/"}]), encoding="utf-8")
        old = Path.cwd()
        os.chdir(self.tmp)
        try:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(["--manifest", str(manifest)])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--manifest", str(manifest), "--confirm-free-license"])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("Registered Mixkit clip", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main([str(self.tmp / "in" / "good.mp4"), "--title", "Restricted clip", "--licence",
                                 "Mixkit Restricted License", "--confirm-free-license",
                                 "--source-url", "https://mixkit.co/free-stock-video/restricted-9/"])
            self.assertEqual(code, 1)
            self.assertIn("Not registered", out.getvalue())
        finally:
            os.chdir(old)


class DiscoveryCheckScriptTests(_Temp):
    def test_only_licensed_results_count_and_unconfigured_providers_are_named(self):
        from scripts.check_footage_sources_live import run_checks
        licensed = _lead("A", "1", "Village", "https://a/1.mp4")
        unlicensed = _lead("A", "2", "Village", "https://a/2.mp4", licence="", licence_url="")
        called = Mock(return_value=[])
        result = run_checks("topic", providers=[
            ("A", lambda query: [licensed, unlicensed]),
            ("Keyed", called, lambda: (False, "missing_key", "KEYED_API_KEY is not set")),
        ])
        by_name = {item["provider"]: item for item in result["providers"]}
        self.assertEqual(by_name["A"]["eligible_candidates"], 3)        # one licensed result for each of 3 queries
        self.assertEqual(by_name["Keyed"]["status"], "not_configured")
        self.assertIn("KEYED_API_KEY", by_name["Keyed"]["reason"])
        called.assert_not_called()
        self.assertEqual(result["providers_with_results"], ["A"])
        self.assertIn("nothing was downloaded", result["important"])

    @needs_ffmpeg
    def test_default_run_names_why_each_provider_returned_nothing(self):
        from scripts.check_footage_sources_live import run_checks
        web = FakeWeb(self.tmp / "web")
        web.add_wikimedia(11, "Topic film", "A film about the topic")
        web.failures["www.loc.gov"] = 429
        web.failure_headers["www.loc.gov"] = {"Retry-After": "3600"}
        web.failures["archive.org"] = requests.ConnectionError("no route")
        web.failures["api.dvidshub.net"] = 403
        with patch("requests.get", web.get), patch.object(fs.time, "sleep"), patch.object(images.time, "sleep"), \
                patch.dict(os.environ, {"DVIDS_API_KEY": "key-dvids-test"}), \
                patch("core.config.EUROPEANA_API_KEY", ""), patch("core.config.NARA_API_KEY", ""), \
                patch.object(images, "PIXABAY_API_KEY", ""), patch.object(images, "COVERR_API_KEY", ""):
            result = run_checks("topic")
        status = {item["provider"]: item["status"] for item in result["providers"]}
        self.assertEqual(status["Wikimedia Commons"], "ok")
        self.assertEqual(status["Library of Congress"], "rate_limited")
        self.assertEqual(status["Internet Archive"], "unreachable")
        self.assertEqual(status["DVIDS"], "auth_failed")
        for name in ("Europeana", "National Archives (NARA)", "Pixabay", "Coverr"):
            self.assertEqual(status[name], "not_configured", name)
        self.assertEqual(result["status"], "passed")
        self.assertNotIn("key-dvids-test", json.dumps(result))


class LiveVerificationScriptTests(_Temp):
    def test_status_comes_from_what_actually_happened(self):
        from scripts import verify_footage_providers as verify
        base = footage_report._new_provider_entry("X")
        cases = [
            (verify.LIVE_VERIFIED, dict(download_successes=1, results_found=3)),
            (verify.NOT_CONFIGURED, dict(auth_status="missing_key", disabled_reason="X_KEY is not set")),
            (verify.AUTH_FAILED, dict(auth_status="rejected", http_status_counts={"403": 1})),
            (verify.RATE_LIMITED, dict(search_status="rate_limited", http_status_counts={"429": 3})),
            (verify.UNREACHABLE, dict(search_status="network_error", failure_reason="ConnectTimeout")),
            (verify.INVALID_MEDIA, dict(results_found=2, download_attempts=2, invalid_media=2)),
            (verify.DOWNLOAD_FAILED, dict(results_found=2, download_attempts=2)),
            (verify.NO_ELIGIBLE_RESULTS, dict(results_found=4, licence_rejections=4)),
            (verify.NO_ELIGIBLE_RESULTS, dict()),
        ]
        for expected, fields in cases:
            with self.subTest(expected=expected, fields=fields):
                self.assertEqual(verify.classify(dict(base, **fields))[0], expected)
        # A search that succeeded is never enough on its own.
        self.assertNotEqual(verify.classify(dict(base, results_found=20, downloadable_candidates=20,
                                                 search_status="ok"))[0], verify.LIVE_VERIFIED)

    @needs_ffmpeg
    def test_offline_run_writes_the_evidence_files(self):
        from scripts import verify_footage_providers as verify
        web = FakeWeb(self.tmp / "web")
        web.add_nasa("KSC-69", "Apollo 11 launch", "Apollo 11 Saturn V launch", size="1280x720")
        web.add_wikimedia(11, "Steam locomotive", "A steam locomotive")
        web.failures["archive.org"] = 503
        out = self.tmp / "verify"
        with patch("requests.get", web.get), patch.object(fs.time, "sleep"), patch.object(images.time, "sleep"), \
                patch.object(verify, "all_providers", lambda: [p for p in fs.archive_providers("space")
                                                               if p.name in ("NASA", "Wikimedia Commons",
                                                                             "Internet Archive", "DVIDS")]), \
                patch.dict(os.environ, {"DVIDS_API_KEY": ""}):
            result = verify.verify_providers(out)
        status = {item["provider"]: item["status"] for item in result["providers"]}
        self.assertEqual(status["NASA"], verify.LIVE_VERIFIED)
        self.assertEqual(status["Wikimedia Commons"], verify.LIVE_VERIFIED)
        self.assertEqual(status["Internet Archive"], verify.UNREACHABLE)
        self.assertEqual(status["DVIDS"], verify.NOT_CONFIGURED)
        self.assertEqual(status["Mixkit"], verify.MANUAL_ONLY)
        self.assertEqual(result["live_verified_count"], 2)
        for name in ("live_verification.json", "live_verification.md", "provider_report.json",
                     "provider_report.md", "licenses.csv", "credits.txt"):
            self.assertTrue((out / name).is_file(), name)
        self.assertTrue((out / "frames" / "nasa.jpg").is_file())
        self.assertEqual(list((out / "clips").glob("*.mp4")), [])      # media is not kept by default
        nasa = next(item for item in result["providers"] if item["provider"] == "NASA")
        self.assertEqual((nasa["asset"]["width"], nasa["asset"]["height"]), (1280, 720))
        self.assertFalse(result["uploaded_or_published"])


if __name__ == "__main__":
    unittest.main()
