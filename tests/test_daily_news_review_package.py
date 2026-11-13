"""Reviewed daily-news render -> shared review package (schema v2)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from scripts.package_daily_news_review import create_package, main
from tests.review_package_fixture import approve, fake_media

SIDECAR = {"provider": "Wikimedia Commons", "page_url": "https://commons.wikimedia.org/wiki/File:Dam.jpg",
           "title": "Dam", "creator": "A. Photographer", "licence": "CC BY-SA 4.0",
           "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
           "attribution_text": "A. Photographer, CC BY-SA 4.0"}


class DailyNewsPackageTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def fixture(self, *, qa_status="PASS", advanced=None, sidecar=True):
        assets = self.root / "assets"
        render = self.root / "rendered"
        assets.mkdir()
        (render / "qa" / "advanced_qa").mkdir(parents=True)
        scenes = []
        for index in range(2):
            asset = assets / f"scene{index}.jpg"
            asset.write_bytes(b"jpeg-%d" % index)
            if sidecar:
                Path(str(asset) + ".source.json").write_text(json.dumps(SIDECAR))
            scenes.append({"say": f"Sentence {index}.", "image": "A dam", "asset_path": str(asset),
                           "source_ids": ["S1"], "ai_disclosure": False})
        script = self.root / "approved_script.json"
        script.write_text(json.dumps({
            "title": "New dam opens", "language": "english", "scenes": scenes,
            "sources": [{"source_id": "S1", "title": "Dam opens", "publisher": "Reuters",
                         "url": "https://www.reuters.com/world/dam-opens", "tier": 1},
                        {"source_id": "S2", "title": "Ministry note", "publisher": "PIB",
                         "url": "https://pib.gov.in/release/1", "tier": 1}]}))
        (render / "review_short.mp4").write_bytes(b"\0" * 4096)
        (render / "captions.ass").write_text("[Events]\n")
        (render / "credits.txt").write_text("Credits\n")
        (render / "production_manifest.json").write_text(json.dumps({"shots": [
            {"shot_id": "scene-01", "narration": "Sentence 0.", "start_seconds": 0.0, "duration_seconds": 4.0},
            {"shot_id": "scene-02", "narration": "Sentence 1.", "start_seconds": 4.0, "duration_seconds": 5.0}]}))
        reports = {"semantic_qa_report": {"status": "REVIEW"}, "audio_quality_report": {"status": "PASS"},
                   "caption_qa_report": {"status": "PASS"}, "scene_asset_provenance_report": {"status": "PASS"}}
        reports.update(advanced or {})
        for name, data in reports.items():
            if data is not None:
                (render / "qa" / "advanced_qa" / f"{name}.json").write_text(json.dumps(data))
        qa = render / "qa" / "technical.json"
        qa.write_text(json.dumps({"status": qa_status, "errors": ["loudness out of range"] if qa_status == "FAIL" else []}))
        return script, render, qa

    def package(self, **kwargs):
        script, render, qa = self.fixture(**kwargs)
        with fake_media("daily_news"):
            root = create_package(script, render, qa, self.root / "review_package")
        return root, json.loads((root / "production_manifest.json").read_text()), \
            json.loads((root / "package_summary.json").read_text())

    def test_reviewed_news_render_becomes_a_reviewable_package(self):
        root, manifest, summary = self.package()
        self.assertEqual(summary["status"], "REVIEW_REQUIRED", summary["errors"])
        self.assertEqual((manifest["schema_version"], manifest["mode"]), (2, "daily_news"))
        self.assertEqual([item["source_id"] for item in manifest["research"]["sources"]], ["S1", "S2"])
        scene = manifest["scenes"][0]
        self.assertEqual(scene["source_ids"], ["S1"])
        self.assertEqual(scene["assets"][0]["provenance"]["origin"], "source")
        self.assertEqual(scene["assets"][0]["rights"]["license"], "CC BY-SA 4.0")
        self.assertEqual(scene["assets"][0]["rights"]["status"], "unreviewed")
        for name in ("video.mp4", "captions.ass", "review_dashboard.html", "review_decisions_template.json",
                     "evidence/approved_script.json", "evidence/technical_qa.json", "evidence/credits.txt"):
            self.assertTrue((root / name).is_file(), name)
        self.assertEqual(contract.find_packages(self.root), [root])
        decisions = approve(root)
        with fake_media("daily_news"):
            self.assertTrue(contract.evaluate_release(root, decisions, expected_mode="daily_news")["allowed"])
            self.assertFalse(contract.evaluate_release(root, decisions, expected_mode="shortform")["allowed"])

    def test_automated_passes_are_not_upgraded_into_approval(self):
        root, _, summary = self.package()
        # every report a human can resolve stays REVIEW even when the tool said PASS
        for name in ("semantic", "audio", "captions", "assets"):
            self.assertIn(summary["qa_statuses"][name], {"REVIEW"}, name)
        self.assertEqual(summary["qa_statuses"]["technical"], "PASS")

    def test_failed_or_missing_checks_make_the_package_unapprovable(self):
        cases = (({"qa_status": "FAIL"}, "technical"),
                 ({"advanced": {"audio_quality_report": {"status": "FAIL"}}}, "audio"),
                 ({"advanced": {"audio_quality_report": None}}, "audio"),
                 ({"advanced": {"scene_asset_provenance_report": {"status": "FAIL"}}}, "assets"))
        for index, (kwargs, report) in enumerate(cases):
            with self.subTest(kwargs=kwargs):
                self.root = Path(self._temp.name) / f"case{index}"
                self.root.mkdir()
                _, _, summary = self.package(**kwargs)
                self.assertEqual(summary["status"], "FAIL")
                self.assertEqual(summary["qa_statuses"][report], "FAIL")

    def test_advisory_checks_that_did_not_run_need_a_human_but_do_not_block(self):
        _, _, summary = self.package(advanced={"semantic_qa_report": {"status": "NOT_RUN"},
                                               "caption_qa_report": None})
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertEqual(summary["qa_statuses"]["semantic"], "REVIEW")
        self.assertEqual(summary["qa_statuses"]["captions"], "REVIEW")

    def test_missing_provenance_or_mismatched_render_fails_closed(self):
        script, render, qa = self.fixture(sidecar=False)
        with fake_media("daily_news"), self.assertRaisesRegex(FileNotFoundError, "provenance sidecar is missing"):
            create_package(script, render, qa, self.root / "review_package")
        (render / "production_manifest.json").write_text(json.dumps({"shots": [{"shot_id": "only-one"}]}))
        with fake_media("daily_news"), self.assertRaisesRegex(ValueError, "shot count does not match"):
            create_package(script, render, qa, self.root / "review_package")
        with self.assertRaisesRegex(ValueError, "dedicated package directory"):
            create_package(script, render, qa, render)

    def test_cli_exit_codes(self):
        script, render, qa = self.fixture()
        args = ["--script", str(script), "--render-dir", str(render), "--qa-report", str(qa),
                "--output-dir", str(self.root / "review_package")]
        with fake_media("daily_news"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
            qa.write_text(json.dumps({"status": "FAIL", "errors": ["black video"]}))
            self.assertEqual(main(args), 2)
            (render / "review_short.mp4").unlink()
            self.assertEqual(main(args), 1)


if __name__ == "__main__":
    unittest.main()
