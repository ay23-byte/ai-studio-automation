import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.package_longform_review import create_package


class LongformReviewPackageTests(unittest.TestCase):
    def _fixture(self, root: Path):
        build = root / "build" / "job_42"
        build.mkdir(parents=True)
        media = build / "shot.mp4"
        media.write_bytes(b"media" * 100)
        (build / "subs.ass").write_text("[Script Info]\n", encoding="utf-8")
        (build / "script.json").write_text(json.dumps({"title": "Test documentary"}), encoding="utf-8")
        (build / "visual_review_report.json").write_text(json.dumps({
            "shots": [{
                "shot_id": "shot_0001", "media_path": str(media), "media_type": "video",
                "narration": "A verified test narration.", "visual_prompt": "A test visual",
                "sample_frames": [], "source": {"provider": "Test Archive", "page_url": "https://example.org/item/1"},
            }]
        }), encoding="utf-8")
        with (build / "licenses.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["shot_id", "source", "url", "licence", "attribution_text", "use_status"])
            writer.writeheader()
            writer.writerow({"shot_id": "shot_0001", "source": "Test Archive", "url": "https://example.org/item/1",
                             "licence": "CC BY 4.0", "attribution_text": "Test credit", "use_status": "eligible_pending_human_review"})
        for name, data in {
            "visual_consistency_report.json": {"status": "PASS"},
            "audio_quality_report.json": {"status": "PASS"},
            "quality_report.json": {"status": "PASS"},
        }.items():
            (build / name).write_text(json.dumps(data), encoding="utf-8")
        video = root / "output" / "longform_42.mp4"
        video.parent.mkdir()
        video.write_bytes(b"video" * 30000)
        return build, video

    @patch("scripts.package_longform_review.subprocess.run")
    @patch("scripts.package_longform_review._probe", return_value={
        "width": 1920, "height": 1080, "duration_seconds": 600.0,
        "video_codec": "h264", "audio_codecs": ["aac"],
    })
    def test_creates_review_required_package_without_approving_rights(self, probe, run,):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build, video = self._fixture(root)
            def fake_run(args, **kwargs):
                if "validate_review_package.py" in args[1]:
                    package = Path(args[2])
                    summary_path = Path(args[4])
                    summary_path.write_text(json.dumps({"status": "REVIEW_REQUIRED", "errors": []}), encoding="utf-8")
                    return type("Result", (), {"returncode": 0, "stderr": ""})()
                if "ffmpeg" in args[0]:
                    Path(args[-1]).write_bytes(b"preview")
                return type("Result", (), {"returncode": 0, "stderr": ""})()
            run.side_effect = fake_run
            package = create_package(build, video)
            manifest = json.loads((package / "production_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["mode"], "longform")
            self.assertEqual(manifest["scenes"][0]["assets"][0]["provenance"]["origin"], "source")
            self.assertEqual(manifest["scenes"][0]["assets"][0]["rights"]["status"], "unknown")
            self.assertTrue((package / "video.mp4").is_file())
            self.assertTrue((package / "captions.ass").is_file())
            self.assertTrue((package / "licenses.csv").is_file())
            self.assertEqual(json.loads((package / "review_checklist.json").read_text())["human_review_status"], "pending")
            probe.assert_called_once()

    def test_missing_visual_media_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build, video = self._fixture(root)
            report = json.loads((build / "visual_review_report.json").read_text())
            report["shots"][0]["media_path"] = str(build / "missing.mp4")
            (build / "visual_review_report.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(FileNotFoundError, "media for shot_0001"):
                create_package(build, video)


if __name__ == "__main__":
    unittest.main()
