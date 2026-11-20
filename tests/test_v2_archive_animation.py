"""Offline tests: reviewed archive selection and optional Manim path."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.production_v2.archive import download_commons
from src.production_v2.config import V2Config
from src.production_v2.editor import _segment_command
from src.production_v2.licenses import AssetLibrary
from src.production_v2.models import Scene, VisualSpec
from src.production_v2.visuals import plan_visuals


def metadata(license_url="https://creativecommons.org/licenses/by/4.0/", url="https://upload.wikimedia.org/example.webm"):
    return {"query": {"pages": {"1": {"title": "File:Example.webm", "imageinfo": [{
        "url": url, "mime": "video/webm", "size": 5, "width": 1920, "height": 1080,
        "extmetadata": {"Artist": {"value": "<b>Alice</b>"}, "LicenseUrl": {"value": license_url}}
    }]}}}}


def fetcher(data):
    def fetch(url, limit, timeout):
        return json.dumps(data).encode() if "w/api.php" in url else b"video"
    return fetch


class ArchiveAnimationTests(unittest.TestCase):
    def test_commons_download_records_attribution_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, record = download_commons("File:Example.webm", Path(tmp) / "asset", "scene_01", fetch=fetcher(metadata()))
            self.assertEqual(path.read_bytes(), b"video")
            self.assertEqual(record.media_type, "video")
            self.assertEqual(record.creator, "Alice")
            self.assertIn("CC BY 4.0", record.attribution_text)
            self.assertEqual(len(record.sha256), 64)
            self.assertTrue(AssetLibrary().add(record).eligible)

    def test_bad_license_and_host_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for data in (metadata("https://creativecommons.org/licenses/by-nc/4.0/"), metadata(url="https://evil.example/file")):
                with self.assertRaises(ValueError):
                    download_commons("File:Example.webm", Path(tmp) / "asset", "s", fetch=fetcher(data))

    def test_manifest_requires_explicit_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "visuals.json"
            manifest.write_text(json.dumps({"s": {"type": "commons", "title": "File:a", "reviewed": False}}))
            config = V2Config.from_env({"PV2_VISUAL_MANIFEST": str(manifest)})
            with self.assertRaisesRegex(ValueError, "review"):
                plan_visuals([Scene("s", "n", VisualSpec("test"))], root, AssetLibrary(), config, "documentary")

    def test_manim_manifest_invokes_renderer_and_registers_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "visuals.json"
            manifest.write_text(json.dumps({"s": {"type": "manim_wave", "reviewed": True}}))
            config = V2Config.from_env({"PV2_VISUAL_MANIFEST": str(manifest)})
            def render(cmd, timeout):
                self.assertIn("manim", cmd)
                output = Path(cmd[cmd.index("--media_dir") + 1]) / "videos" / "WaveIllustration.mp4"
                output.parent.mkdir(parents=True)
                output.write_bytes(b"video")
            with patch("src.production_v2.ffmpeg_io.run", side_effect=render):
                assets = plan_visuals([Scene("s", "n", VisualSpec("wave", "animation"))], root, AssetLibrary(), config, "physics_short")
            self.assertEqual(assets[0].record.media_type, "video")
            self.assertTrue(assets[0].record.synthetic)

    def test_blender_orbit_renders_frames_and_registers_video(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "visuals.json"
            manifest.write_text(json.dumps({"s": {"type": "blender_orbit", "reviewed": True}}))
            config = V2Config.from_env({"PV2_VISUAL_MANIFEST": str(manifest)})
            config.rules["physics_short"] = replace(config.rules["physics_short"], fps=1)
            def render(cmd, timeout):
                if "--background" in cmd:
                    prefix = Path(cmd[cmd.index("--output") + 1])
                    for i in range(1, 5):
                        prefix.with_name(prefix.name + f"{i:04d}.png").write_bytes(b"frame")
                else:
                    Path(cmd[-1]).write_bytes(b"video")
            with patch("src.production_v2.ffmpeg_io.run", side_effect=render), \
                 patch("src.production_v2.ffmpeg_io.resolve_ffmpeg", return_value="ffmpeg"):
                assets = plan_visuals([Scene("s", "n", VisualSpec("orbit", "animation"))], root, AssetLibrary(), config, "physics_short")
            self.assertEqual(assets[0].record.media_type, "video")
            self.assertIn("not to scale", assets[0].record.restrictions)
            self.assertFalse(list(assets[0].image_path.parent.glob("frame_*.png")))

    def test_blender_missing_frames_fails_closed(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "visuals.json"
            manifest.write_text(json.dumps({"s": {"type": "blender_orbit", "reviewed": True}}))
            config = V2Config.from_env({"PV2_VISUAL_MANIFEST": str(manifest)})
            config.rules["physics_short"] = replace(config.rules["physics_short"], fps=1)
            with patch("src.production_v2.ffmpeg_io.run"), \
                 patch("src.production_v2.ffmpeg_io.resolve_ffmpeg", return_value="ffmpeg"):
                with self.assertRaisesRegex(ValueError, "every expected"):
                    plan_visuals([Scene("s", "n", VisualSpec("orbit", "diagram"))], root, AssetLibrary(), config, "physics_short")

    def test_blender_cannot_be_selected_for_unrelated_scene_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "visuals.json"
            manifest.write_text(json.dumps({"s": {"type": "blender_orbit", "reviewed": True}}))
            config = V2Config.from_env({"PV2_VISUAL_MANIFEST": str(manifest)})
            with self.assertRaisesRegex(ValueError, "only allowed"):
                plan_visuals([Scene("s", "n", VisualSpec("title", "illustrative"))], root, AssetLibrary(), config, "documentary")

    def test_video_uses_looping_video_input_not_image_loop(self):
        cmd = _segment_command("ffmpeg", Path("asset.webm"), Path("speech.wav"), Path("out.mp4"), 3, 1080, 1920, 30, is_video=True)
        self.assertIn("-stream_loop", cmd)
        self.assertNotIn("-loop", cmd)


if __name__ == "__main__":
    unittest.main()
