import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.news_short_production import _render_media_timeline


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MediaTimelineIntegrationTests(unittest.TestCase):
    def test_downloaded_video_asset_is_used_as_ffmpeg_timeline_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            output = root / "rendered_visuals.mp4"
            work = root / "work"
            work.mkdir()
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-t", "1.5", "-pix_fmt", "yuv420p", str(source)
            ], check=True)
            report = _render_media_timeline(
                [{"asset_path": str(source), "asset_type": "video"}],
                [1.5], output, work
            )
            self.assertTrue(output.is_file())
            self.assertEqual(report["renderer"], "ffmpeg_media_asset_timeline")
            self.assertEqual(report["actual_assets_rendered"], 1)
            segment = report["segments"][0]
            self.assertEqual(segment["asset_path"], str(source.resolve()))
            self.assertTrue(segment["source_sha256"])
            probe = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,width,height:format=duration", "-of", "json", str(output)
            ], text=True)
            info = json.loads(probe)
            video = next(s for s in info["streams"] if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1080, 1920))
            self.assertGreater(float(info["format"]["duration"]), 1.0)


if __name__ == "__main__":
    unittest.main()
