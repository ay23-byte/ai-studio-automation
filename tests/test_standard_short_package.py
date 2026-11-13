"""Pipeline-built Short -> shared review package (schema v2)."""
import json
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from scripts.package_standard_short_review import create_review_package, main
from tests.review_package_fixture import approve, fake_media

NASA = {"provider": "NASA", "page_url": "https://images.nasa.gov/details/PIA00001", "title": "Mars",
        "creator": "NASA/JPL", "licence": "Public Domain", "licence_url": "https://www.nasa.gov/media-guidelines",
        "attribution_text": "NASA/JPL"}
CARD = {"provider": "LabLens/AI Studio local graphic renderer", "asset_type": "programmatic_graphic",
        "ai_generated": False, "fallback": True, "fallback_kind": "local_explainer_card",
        "licence": "Project-created original graphic"}


class StandardShortPackageTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.script = {"title": "Why Mars is red", "description": "Iron oxide.", "language": "english",
                       "scenes": [{"say": "Mars looks red.", "image": "Mars from orbit"},
                                  {"say": "The reason is rust.", "image": "Rusty iron surface"}]}

    def build(self, sidecars=(NASA, NASA), files=("img0.jpg", "raw1.mp4"), quality=None, fallback=None,
              narration=None, verification=None):
        build = self.root / "build" / "job_7"
        build.mkdir(parents=True)
        assets = []
        for index, (name, sidecar) in enumerate(zip(files, sidecars)):
            (build / name).write_bytes(b"media-%d" % index)
            if sidecar is not None:
                (build / (name + ".source.json")).write_text(json.dumps(sidecar))
            assets.append({"scene": index + 1, "file": name})
        (build / "asset_manifest.json").write_text(json.dumps({"assets": assets}))
        (build / "subs.ass").write_text("[Events]\n")
        (build / "script.json").write_text(json.dumps(self.script))
        (build / "scene_timing.json").write_text(json.dumps(
            [{"scene": 1, "start": 0.0, "duration": 3.0}, {"scene": 2, "start": 3.0, "duration": 4.0}]))
        (build / "quality_report.json").write_text(json.dumps(quality or {"status": "PASS"}))
        if fallback:
            (build / "visual_fallback_report.json").write_text(json.dumps(fallback))
        (build / "narration_synthesis_report.json").write_text(json.dumps(narration or {
            "requested_engine": "edge", "actual_engine": "edge", "fallback_reason": None,
            "voice": "en-GB-RyanNeural", "language": "english"}))
        if verification:
            (build / "verification.json").write_text(json.dumps(verification))
        video = self.root / "output" / "shorts_7.mp4"
        video.parent.mkdir(exist_ok=True)
        video.write_bytes(b"\0" * 4096)
        return build, video

    def package(self, mode="shortform", **kwargs):
        build, video = self.build(**kwargs)
        with fake_media(mode):
            root = create_review_package(self.script, build, video=video, mode=mode, pipeline_job_id=7)
        return root, json.loads((root / "production_manifest.json").read_text()), \
            json.loads((root / "package_summary.json").read_text())

    def test_sourced_short_becomes_a_reviewable_package(self):
        root, manifest, summary = self.package()
        self.assertEqual(summary["status"], "REVIEW_REQUIRED", summary["errors"])
        self.assertEqual(manifest["mode"], "shortform")
        first, second = manifest["scenes"]
        self.assertEqual(first["narration"], "Mars looks red.")
        self.assertEqual((first["start_seconds"], second["start_seconds"]), (0.0, 3.0))
        self.assertEqual(first["assets"][0]["provenance"]["origin"], "source")
        self.assertEqual(first["assets"][0]["rights"]["license"], "Public Domain")
        self.assertEqual(first["assets"][0]["rights"]["status"], "unreviewed")
        self.assertEqual(second["assets"][0]["asset_type"], "video")
        self.assertIn("#Shorts", manifest["publish"]["title"])
        self.assertEqual(manifest["narration"]["provider"], "edge")
        self.assertFalse(manifest["narration"]["fallback_used"])
        with fake_media():
            self.assertTrue(contract.evaluate_release(root, approve(root), expected_mode="shortform")["allowed"])

    def test_unknown_provenance_is_recorded_as_unknown_and_blocks_release(self):
        root, manifest, summary = self.package(sidecars=(None, NASA))
        self.assertEqual(manifest["scenes"][0]["assets"][0]["provenance"]["origin"], "unknown")
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        with fake_media():
            report = contract.evaluate_release(root, approve(root))
        self.assertIn("origin is unknown", "\n".join(report["blocking"]))

    def test_fallback_cards_are_found_and_a_card_slideshow_is_not_approvable(self):
        root, manifest, summary = self.package(
            sidecars=(CARD, CARD), files=("card0.jpg", "card1.jpg"),
            fallback={"status": "FAIL", "fallback_card_count": 2, "visual_count": 2})
        self.assertEqual(manifest["scenes"][0]["assets"][0]["provenance"]["origin"], "project_graphic")
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(summary["qa_statuses"]["assets"], "FAIL")
        self.assertIn("card slideshow", (root / "qa" / "assets.json").read_text())

    def test_robotic_fallback_voice_is_never_silently_accepted(self):
        root, manifest, summary = self.package(narration={
            "requested_engine": "edge", "actual_engine": "espeak-ng", "language": "english",
            "fallback_reason": "All Edge TTS voices failed", "voice": "espeak-ng offline fallback"})
        self.assertTrue(manifest["narration"]["fallback_used"])
        self.assertEqual(summary["qa_statuses"]["audio"], "FAIL")
        self.assertIn("robotic fallback voice", (root / "qa" / "audio.json").read_text())

    def test_pipeline_quality_failure_or_upload_block_carries_over(self):
        for index, quality in enumerate(({"status": "FAIL", "issues": [{"message": "black frames"}]},
                                         {"status": "REVIEW", "upload_blocked": True, "upload_block_reason": "x"})):
            with self.subTest(quality=quality):
                self.root = Path(self._temp.name) / f"q{index}"
                self.root.mkdir()
                _, _, summary = self.package(quality=quality)
                self.assertEqual(summary["qa_statuses"]["technical"], "FAIL")

    def test_news_short_carries_its_sources_for_review(self):
        verification = {"status": "PASS", "method": "source-tier gate", "sources": [
            {"source_id": "N1", "title": "Headline", "publisher": "Reuters", "url": "https://reuters.com/a"},
            {"source_id": "N2", "title": "Headline 2", "publisher": "AP", "url": "https://apnews.com/b"}]}
        root, manifest, _ = self.package(mode="daily_news", verification=verification)
        self.assertEqual(manifest["mode"], "daily_news")
        self.assertEqual([item["source_id"] for item in manifest["research"]["sources"]], ["N1", "N2"])
        decisions = approve(root)
        decisions["source_reviews"][1]["decision"] = "pending"
        with fake_media("daily_news"):
            report = contract.evaluate_release(root, decisions)
        self.assertIn("research source N2", "\n".join(report["blocking"]))

    def test_missing_visual_or_captions_fail_closed(self):
        build, video = self.build()
        (build / "raw1.mp4").unlink()
        with fake_media(), self.assertRaisesRegex(FileNotFoundError, r"scene\(s\): 2"):
            create_review_package(self.script, build, video=video)
        (build / "subs.ass").write_text("")
        with fake_media(), self.assertRaisesRegex(FileNotFoundError, "captions file is missing or empty"):
            create_review_package(self.script, build, video=video)

    def test_cli_and_legacy_render_dir_call(self):
        build, video = self.build()
        with fake_media():
            self.assertEqual(main(["--build-dir", str(build), "--video", str(video)]), 0)
            self.assertEqual(main(["--build-dir", str(build), "--video", str(self.root / "none.mp4")]), 1)
        render = self.root / "render"
        render.mkdir()
        (render / "video.mp4").write_bytes(b"\0" * 4096)
        (render / "captions.ass").write_text("[Events]\n")
        with fake_media():
            package = create_review_package(self.script, build, render)
        self.assertEqual(package, (render / "shared_review_package").resolve())


if __name__ == "__main__":
    unittest.main()
