"""Milestone 4: audio analysis, mixing and loudness handling."""
from __future__ import annotations

import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from src.production_v2.audio import analyze_wav, concat_segments, loudness_normalize
from src.production_v2.config import V2Config
from src.production_v2.models import NarrationSegment


def write_wave(path: Path, seconds: float, *, silent: bool = False, clipping: bool = False) -> None:
    rate = 16_000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        for i in range(int(rate * seconds)):
            if silent:
                value = 0
            elif clipping:
                value = 32767 if i % 2 else -32768
            else:
                value = int(6000 * math.sin(2 * math.pi * 220 * i / rate))
            handle.writeframes(struct.pack("<h", value))


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = V2Config.from_env({})

    def tearDown(self):
        self.tmp.cleanup()

    def test_concat_places_segments_on_a_shared_timeline(self):
        first = self.root / "a.wav"
        second = self.root / "b.wav"
        write_wave(first, 1.0)
        write_wave(second, 1.0)
        segments = [NarrationSegment("a", "a", 0, 1, str(first), "mock"),
                    NarrationSegment("b", "b", 2, 3, str(second), "mock")]
        out = self.root / "mix.wav"
        info = concat_segments(segments, out, min_duration=4.0)
        self.assertEqual(info["duration_seconds"], 4.0)
        self.assertEqual(analyze_wav(out)["status"], "PASS")

    def test_long_silence_and_clipping_are_detected(self):
        silent = self.root / "silent.wav"
        clipped = self.root / "clipped.wav"
        write_wave(silent, 7.0, silent=True)
        write_wave(clipped, 1.0, clipping=True)
        self.assertIn("silent interval", " ".join(analyze_wav(silent)["issues"]))
        self.assertEqual(analyze_wav(clipped)["status"], "FAIL")

    def test_loudness_normalize_is_honest_when_ffmpeg_unavailable(self):
        source = self.root / "in.wav"
        target = self.root / "out.wav"
        write_wave(source, 1.0)
        with mock.patch("src.production_v2.audio.resolve_ffmpeg", return_value=None):
            result = loudness_normalize(source, target, self.config)
        self.assertFalse(result["applied"])
        self.assertEqual(target.read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
