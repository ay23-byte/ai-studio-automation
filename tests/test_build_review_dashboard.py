"""The offline review dashboard: real content, no auto-approval, no script injection."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from core.review_dashboard import dashboard_data
from scripts.build_review_dashboard import build_dashboard
from tests.review_package_fixture import fake_media, make_package, source_asset


class ReviewDashboardTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def page(self, **kwargs):
        self.manifest = make_package(self.root, **kwargs)
        return (self.root / "review_dashboard.html").read_text(encoding="utf-8")

    def island(self, page):
        match = re.search(r'<script type="application/json" id="package-data">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_page_carries_the_job_video_hash_shots_sources_and_qa(self):
        page = self.page()
        data = self.island(page)
        self.assertEqual(data["manifest"]["job_id"], self.manifest["job_id"])
        self.assertEqual(data["manifest"]["video"]["sha256"], self.manifest["video"]["sha256"])
        self.assertEqual(len(data["manifest"]["scenes"]), 2)
        asset = data["manifest"]["scenes"][0]["assets"][0]
        self.assertEqual(asset["provenance"]["source_url"], "https://images.nasa.gov/details/KSC-69PC-442")
        self.assertEqual(asset["rights"]["license"], "Public Domain")
        self.assertEqual(data["qa"]["technical"]["status"], "PASS")
        self.assertEqual(data["qa"]["semantic"]["status"], "REVIEW")
        self.assertIn('id="main-video"', page)
        self.assertIn("review_decisions", page)

    def test_template_and_page_start_with_nothing_approved(self):
        page = self.page()
        template = json.loads((self.root / "review_decisions_template.json").read_text())
        self.assertEqual(template, self.island(page)["template"])
        self.assertEqual(template["schema_version"], contract.DECISIONS_SCHEMA_VERSION)
        self.assertEqual(template["video_sha256"], self.manifest["video"]["sha256"])
        self.assertEqual(template["job_id"], self.manifest["job_id"])
        self.assertEqual(template["manifest_fingerprint"], self.manifest["fingerprint"])
        self.assertEqual(template["overall"]["decision"], "pending")
        self.assertEqual({item["decision"] for item in template["scene_reviews"]}, {"pending"})
        self.assertEqual({item["rights_decision"] for item in template["asset_reviews"]}, {"pending"})
        self.assertEqual(set(template["confirmations"]), set(contract.CONFIRMATIONS))
        self.assertEqual(set(template["confirmations"].values()), {"pending"})
        with fake_media():
            self.assertFalse(contract.evaluate_release(self.root, template)["allowed"])
        # There is no control that approves everything at once.
        self.assertNotRegex(page.lower(), r"approve[ _-]all|select[ _-]all")

    def test_hostile_text_cannot_inject_markup_or_script(self):
        hostile = '</script><script>alert(1)</script><img src=x onerror=alert(2)><!-- '
        page = self.page(assets=[source_asset(provenance={"title": hostile, "creator": hostile}), source_asset()])
        self.assertEqual(page.count("</script>"), 2)           # the data island and the program, nothing else
        island = page.split('id="package-data">')[1].split("</script>")[0]
        self.assertNotIn("<!--", island)
        self.assertNotIn("</", island)                         # nothing can close the data element early
        self.assertNotIn("<img src=x", page.replace(island, ""))   # the text exists only as inert JSON data
        self.assertEqual(self.island(page)["manifest"]["scenes"][0]["assets"][0]["provenance"]["title"], hostile)
        self.assertIn("default-src 'none'", page)              # no network access from the page

    def test_assets_without_rights_evidence_are_marked_blocked(self):
        page = self.page(assets=[source_asset(rights={"license": None}), source_asset()])
        problems = self.island(page)["rights_evidence_problems"]
        self.assertTrue(any("no licence was recorded" in item for item in problems["scene-01:a1"]))
        self.assertEqual(problems["scene-02:a1"], [])

    def test_v1_package_gets_no_dashboard(self):
        (self.root / "production_manifest.json").write_text(json.dumps(
            {"schema_version": 1, "mode": "shortform", "topic": "T", "video_file": "video.mp4",
             "captions_file": "c.srt", "scenes": []}))
        with self.assertRaisesRegex(contract.ContractError, "schema v2"):
            build_dashboard(self.root)
        with self.assertRaises(contract.ContractError):
            dashboard_data(self.root)


if __name__ == "__main__":
    unittest.main()
