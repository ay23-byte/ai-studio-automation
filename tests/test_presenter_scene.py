"""CPU-only presenter renderer tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.presenter_scene import render_presenter_scene


class PresenterSceneTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"PRESENTER_ENABLED": "0"}, clear=False):
            self.assertIsNone(render_presenter_scene(1, 4, tempfile.mkdtemp()))

    def test_missing_portrait_skips_cleanly(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
            "PRESENTER_ENABLED": "1",
            "PRESENTER_IMAGE": str(Path(td) / "missing.png"),
        }, clear=False):
            self.assertIsNone(render_presenter_scene(1, 4, td))

    def test_small_or_invalid_portrait_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "small.png"
            Image.new("RGB", (100, 100)).save(p)
            with patch.dict(os.environ, {
                "PRESENTER_ENABLED": "1", "PRESENTER_IMAGE": str(p)
            }, clear=False):
                with self.assertRaises(ValueError):
                    render_presenter_scene(1, 4, td)

    def test_valid_portrait_invokes_ffmpeg_and_checks_duration(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "portrait.png"
            Image.new("RGB", (1024, 1024)).save(p)
            calls = []
            def fake_run(cmd, check, timeout=None):
                calls.append(cmd)
                Path(cmd[-1]).write_bytes(b"fake mp4")
            def fake_output(cmd, timeout=None):
                return b"4.000"
            with patch.dict(os.environ, {
                "PRESENTER_ENABLED": "1", "PRESENTER_IMAGE": str(p)
            }, clear=False), patch("core.presenter_scene.subprocess.run", side_effect=fake_run), patch(
                "core.presenter_scene.subprocess.check_output", side_effect=fake_output
            ):
                result = render_presenter_scene(1, 4, td)
            self.assertTrue(result.name.endswith(".mp4"))
            self.assertTrue(any("zoompan" in str(arg) for arg in calls[0]))


if __name__ == "__main__":
    unittest.main()
