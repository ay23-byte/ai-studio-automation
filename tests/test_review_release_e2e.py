"""End to end with real FFmpeg: build output -> package -> dashboard -> decisions -> private release.

One test per production mode.  The packaging step runs as a subprocess from a
foreign working directory with no PYTHONPATH, exactly as a workflow runs it
(that is how the documentary packaging step failed in production: a lazily
imported ``scripts.`` module could not be found).  YouTube is replaced by a fake
uploader; nothing leaves the machine.
"""
import ast
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from scripts import release_review_package as release
from scripts.apply_review_decisions import DecisionsRejected, apply_decisions
from tests.review_package_fixture import FakeUploader, approve, have_ffmpeg, render_real_video

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = {"provider": "NASA", "page_url": "https://images.nasa.gov/details/PIA00001", "title": "Mars",
           "creator": "NASA/JPL", "licence": "Public Domain", "licence_url": "https://www.nasa.gov/media-guidelines",
           "attribution_text": "NASA/JPL"}


class ScriptBootstrapTests(unittest.TestCase):
    def test_every_script_that_imports_the_repo_can_find_it(self):
        """A script run by path gets ``scripts/`` on sys.path, not the repository root.

        ``--help`` does not catch a lazy ``from scripts.x import y`` inside a
        function, so the source is inspected: any script that imports ``core``
        or ``scripts`` anywhere must add the repository root to sys.path.
        """
        missing = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imports_repo = any(
                (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in {"core", "scripts"})
                or (isinstance(node, ast.Import) and any(alias.name.split(".")[0] in {"core", "scripts"}
                                                         for alias in node.names))
                for node in ast.walk(tree))
            if imports_repo and "sys.path.insert(0" not in source:
                missing.append(path.name)
        self.assertEqual(missing, [])


@unittest.skipUnless(have_ffmpeg(), "FFmpeg/FFprobe are required")
class ReviewReleaseEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.build_root, self.output_root = self.root / "build", self.root / "output"
        self.build = self.build_root / "job_5"
        self.build.mkdir(parents=True)
        self.output_root.mkdir()

    # ---- build outputs, as the pipelines write them -------------------------------
    def _short_build(self, mode):
        script = {"title": "Why Mars is red", "description": "Iron oxide.", "language": "english",
                  "scenes": [{"say": "Mars looks red.", "image": "Mars"}, {"say": "It is rust.", "image": "Rust"}]}
        assets = []
        for index in range(2):
            name = f"img{index}.jpg"
            (self.build / name).write_bytes(b"jpeg-%d" % index)
            (self.build / f"{name}.source.json").write_text(json.dumps(SIDECAR))
            assets.append({"scene": index + 1, "file": name})
        (self.build / "asset_manifest.json").write_text(json.dumps({"assets": assets}))
        (self.build / "script.json").write_text(json.dumps(script))
        (self.build / "subs.ass").write_text("[Script Info]\n[Events]\n")
        (self.build / "quality_report.json").write_text(json.dumps({"status": "PASS", "score": 96}))
        (self.build / "narration_synthesis_report.json").write_text(json.dumps(
            {"requested_engine": "edge", "actual_engine": "edge", "voice": "en-GB-RyanNeural", "language": "english"}))
        if mode == "daily_news":
            (self.build / "verification.json").write_text(json.dumps({"status": "PASS", "sources": [
                {"source_id": "N1", "title": "Mars study", "publisher": "Reuters", "url": "https://reuters.com/x"},
                {"source_id": "N2", "title": "Mars study", "publisher": "AP", "url": "https://apnews.com/y"}]}))
        render_real_video(self.output_root / "shorts_5.mp4", mode)

    def _documentary_build(self):
        media = self.build / "shot_0001.mp4"
        media.write_bytes(b"clip" * 64)
        (self.build / "script.json").write_text(json.dumps({"title": "A test documentary", "description": "Test."}))
        (self.build / "subs.ass").write_text("[Script Info]\n[Events]\n")
        (self.build / "credits.txt").write_text("Credits\nNASA/JPL, Public Domain\n")
        (self.build / "visual_review_report.json").write_text(json.dumps({"issues": [], "shots": [{
            "shot_id": "shot_0001", "media_path": str(media), "media_type": "video",
            "narration": "The probe left Earth in 1977.", "visual_prompt": "A probe", "start_seconds": 0.0,
            "duration_seconds": 3.0, "sample_frames": [],
            "source": {"provider": "NASA", "page_url": SIDECAR["page_url"], "title": "Probe", "creator": "NASA/JPL"},
            "semantic_screening": {"status": "match", "match_score": 0.9}}]}))
        with (self.build / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["shot_id", "source", "url", "author", "licence",
                                                        "attribution_text", "licence_url", "use_status",
                                                        "fallback_required"])
            writer.writeheader()
            writer.writerow({"shot_id": "shot_0001", "source": "NASA", "url": SIDECAR["page_url"],
                             "author": "NASA/JPL", "licence": "Public Domain", "attribution_text": "NASA/JPL",
                             "licence_url": SIDECAR["licence_url"], "use_status": "eligible_pending_human_review",
                             "fallback_required": "False"})
        reports = {
            "visual_consistency_report.json": {"status": "PASS", "summary": {"fail_count": 0, "review_count": 0}},
            "quality_report.json": {"status": "PASS"}, "audio_quality_report.json": {"status": "PASS"},
            "narration_synthesis_report.json": {"requested_engine": "edge", "actual_engine": "edge",
                                                "voice": "en-GB-SoniaNeural", "language": "english"},
            "research_verification_report.json": {"sources": [{
                "source_id": "S1", "title": "Mission page", "publisher": "NASA", "url": "https://science.nasa.gov/v",
                "url_valid": True, "metadata_complete": True}], "claims": [{
                    "claim_id": "C1", "text": "The probe launched in 1977.", "evidence": "Mission page.",
                    "valid_source_ids": ["S1"], "traceability_status": "source_linked_pending_human_review"}]}}
        for name, data in reports.items():
            (self.build / name).write_text(json.dumps(data))
        render_real_video(self.output_root / "longform_5.mp4", "longform")

    # ---- the workflow steps -------------------------------------------------------
    def _package_like_a_workflow(self, mode):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env.update({"GITHUB_RUN_ID": "7001", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_STEP_SUMMARY": str(self.root / "s.md"),
                    "AI_STUDIO_DB_PATH": str(self.root / "db.sqlite"), "AI_STUDIO_STATE_DIR": str(self.root / "state")})
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "package_build_for_review.py"), "--mode", mode,
             "--build-root", str(self.build_root), "--output-root", str(self.output_root)],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        packages = contract.find_packages(self.root)
        self.assertEqual(len(packages), 1)
        return packages[0]

    def _review_and_release(self, mode):
        package = self._package_like_a_workflow(mode)
        manifest = json.loads((package / "production_manifest.json").read_text())
        sha = manifest["video"]["sha256"]
        self.assertEqual(manifest["job_id"], f"{mode}-run7001-a1-j5")
        self.assertEqual(sha, contract.sha256_file(package / "video.mp4"))
        self.assertTrue(list((package / "preview" / "frames").glob("*.jpg")))        # real frames were extracted
        self.assertIn("7001", (self.root / "s.md").read_text())
        dashboard = (package / "review_dashboard.html").read_text()
        self.assertIn(sha, dashboard)
        self.assertNotIn('checked', dashboard.split("<script")[0])                  # nothing is pre-approved

        # 1. untouched template: refused, and nothing is written
        template = package / contract.TEMPLATE_NAME
        with self.assertRaises(DecisionsRejected):
            apply_decisions(package, template)
        self.assertFalse((package / contract.DECISIONS_NAME).exists())
        uploader = FakeUploader()
        with self.assertRaises(release.ReleaseBlocked):
            release.release_package(package, json.loads(template.read_text()), sha, True, expected_mode=mode,
                                    source_run_id="7001", report_dir=self.root / "r0", uploader=uploader)
        self.assertEqual(uploader.uploads, [])
        self.assertEqual(json.loads((self.root / "r0" / "release_report.json").read_text())["outcome"], "blocked")

        # 2. complete decisions: uploaded private once, privacy read back
        decisions = approve(package)
        report = release.release_package(package, decisions, sha, True, expected_mode=mode, source_run_id="7001",
                                         report_dir=self.root / "r1", uploader=uploader)
        self.assertEqual(report["outcome"], "uploaded_private_verified")
        self.assertEqual(len(uploader.uploads), 1)
        upload = uploader.uploads[0]
        self.assertEqual(upload["privacy_status"], "private")
        self.assertIn(f"AI-STUDIO-REVIEWED-SHA256:{sha}", upload["description"])
        self.assertEqual(contract.sha256_file(upload["video_path"]), sha)            # the reviewed bytes, not a copy
        for name in ("release_report.json", "release_report.md", "youtube-upload-result.json"):
            self.assertTrue((self.root / "r1" / name).is_file(), name)

        # 3. the same release again reuses the existing private upload
        again = release.release_package(package, decisions, sha, True, expected_mode=mode, source_run_id="7001",
                                        report_dir=self.root / "r2", uploader=uploader)
        self.assertEqual(len(uploader.uploads), 1)
        self.assertTrue(again["upload"]["reused_existing_upload"])

        # 4. a re-encoded video invalidates the approval
        render_real_video(package / "video.mp4", mode, seconds=4)
        fresh = FakeUploader()
        with self.assertRaises(release.ReleaseBlocked):
            release.release_package(package, decisions, sha, True, expected_mode=mode, source_run_id="7001",
                                    report_dir=self.root / "r3", uploader=fresh)
        self.assertEqual(fresh.uploads, [])
        return package

    def test_standard_short(self):
        self._short_build("shortform")
        self._review_and_release("shortform")

    def test_daily_news_short(self):
        self._short_build("daily_news")
        package = self._review_and_release("daily_news")
        manifest = json.loads((package / "production_manifest.json").read_text())
        self.assertEqual(len(manifest["research"]["sources"]), 2)

    def test_documentary(self):
        self._documentary_build()
        package = self._review_and_release("longform")
        manifest = json.loads((package / "production_manifest.json").read_text())
        self.assertEqual(manifest["research"]["claims"][0]["claim_id"], "C1")


if __name__ == "__main__":
    unittest.main()
