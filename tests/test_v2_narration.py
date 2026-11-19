"""Milestone 4: free narration adapters and pronunciation handling."""
from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from src.production_v2.config import V2Config
from src.production_v2.models import Scene, Script, VisualSpec
from src.production_v2.narration import MockNarrator, apply_pronunciation, select_narrator


def script() -> Script:
    return Script("physics_short", "T", "Q", "Hook", [
        Scene("s1", "GPU and voltage explained", VisualSpec("diagram", "diagram")),
        Scene("s2", "A second explanatory sentence", VisualSpec("diagram", "diagram")),
    ], "Takeaway")


class NarrationTests(unittest.TestCase):
    def test_pronunciation_map_applies_whole_words(self):
        self.assertEqual(apply_pronunciation("GPU and GPUX", {"GPU": "G P U"}),
                         "G P U and GPUX")

    def test_mock_narrator_produces_timed_wav_segments(self):
        with tempfile.TemporaryDirectory() as tmp:
            segments = MockNarrator().synthesize(script(), Path(tmp), V2Config.from_env({}))
            self.assertEqual(len(segments), 2)
            self.assertLess(segments[0].end, segments[1].end + 0.001)
            for segment in segments:
                path = Path(segment.audio_path)
                self.assertTrue(path.is_file())
                with wave.open(str(path), "rb") as handle:
                    self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(segments[0].voice, "mock_offline_tone")

    def test_provider_selection_is_free_and_labelled(self):
        narrator, warnings = select_narrator(V2Config.from_env({"PV2_PROVIDER": "mock"}))
        self.assertEqual(narrator.backend, "mock_offline_tone")
        self.assertTrue(any("not publication" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
