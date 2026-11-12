import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.package_standard_short_review import create_review_package


class StandardShortPackageTests(unittest.TestCase):
    def test_package_records_unknown_rights_as_review_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root / "build"
            render = root / "render"
            build.mkdir()
            render.mkdir()
            (build / "img0.jpg").write_bytes(b"image")
            (render / "video.mp4").write_bytes(b"video")
            (render / "captions.ass").write_text("[Events]\n", encoding="utf-8")

            def fake_run(args, **kwargs):
                if args[0] == "ffprobe":
                    return type("Result", (), {"returncode": 0, "stdout": json.dumps({
                        "streams": [{"codec_type": "video", "width": 1080, "height": 1920},
                                    {"codec_type": "audio"}], "format": {"duration": "10"}}), "stderr": ""})()
                if args[0] == "ffmpeg":
                    Path(args[-1]).write_bytes(b"frame")
                    return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                if args[0] == "python":
                    package = Path(args[3])
                    manifest = json.loads((package / "production_manifest.json").read_text())
                    self.assertEqual(manifest["scenes"][0]["assets"][0]["provenance"]["origin"], "unknown")
                    (package / "package_summary.json").write_text(json.dumps({"status": "REVIEW_REQUIRED"}))
                    return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                raise AssertionError(args[0])

            with patch("scripts.package_standard_short_review.subprocess.run", side_effect=fake_run):
                package = create_review_package({"title": "Test", "scenes": [{"say": "Narration"}]}, build, render)
            self.assertTrue((package / "production_manifest.json").is_file())
            self.assertTrue((package / "review_checklist.json").is_file())


if __name__ == "__main__":
    unittest.main()
