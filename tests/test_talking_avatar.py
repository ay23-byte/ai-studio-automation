"""Tests for optional Talking Avatar clip resolution."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.talking_avatar import find_section_clip, probe_duration


class TalkingAvatarClipTests(unittest.TestCase):
    def test_missing_clip_returns_none(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"AVATAR_CLIPS_DIR": td}):
            self.assertIsNone(find_section_clip(1))

    def test_finds_numbered_mp4(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"AVATAR_CLIPS_DIR": td}):
            expected = Path(td) / "presenter_section_02.mp4"
            expected.write_bytes(b"fake")
            self.assertEqual(find_section_clip(2), expected)

    def test_prefers_mp4_over_webm(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"AVATAR_CLIPS_DIR": td}):
            mp4 = Path(td) / "presenter_section_01.mp4"
            webm = Path(td) / "presenter_section_01.webm"
            mp4.write_bytes(b"mp4")
            webm.write_bytes(b"webm")
            self.assertEqual(find_section_clip(1), mp4)

    def test_probe_duration(self):
        with patch("core.talking_avatar.subprocess.check_output", return_value="12.5"):
            self.assertEqual(probe_duration("clip.mp4"), 12.5)


if __name__ == "__main__":
    unittest.main()
