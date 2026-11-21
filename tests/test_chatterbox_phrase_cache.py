import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import voice


class ChatterboxPhraseCacheTests(unittest.TestCase):
    def test_completed_phrase_is_reused_on_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            calls = []

            def fake_synth(text, language, path):
                calls.append((text, language))
                path.write_bytes(b"RIFF" + b"0" * 2048)

            with patch.dict(os.environ, {"CHATTERBOX_CACHE_DIR": str(cache)}):
                with patch.object(voice, "_synth_chatterbox", side_effect=fake_synth):
                    with patch.object(voice, "_dur", return_value=2.5):
                        first = voice._synth_all_chatterbox(
                            ["First sentence.", "Second sentence."], "english", Path(tmp)
                        )
                        second = voice._synth_all_chatterbox(
                            ["First sentence.", "Second sentence."], "english", Path(tmp)
                        )

            self.assertEqual(len(calls), 2)
            self.assertEqual(first, second)
            self.assertTrue(all(path.is_file() for path in second))

    def test_changed_text_does_not_reuse_old_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            calls = []

            def fake_synth(text, language, path):
                calls.append(text)
                path.write_bytes(b"RIFF" + b"0" * 2048)

            with patch.dict(os.environ, {"CHATTERBOX_CACHE_DIR": str(cache)}):
                with patch.object(voice, "_synth_chatterbox", side_effect=fake_synth):
                    with patch.object(voice, "_dur", return_value=2.5):
                        voice._synth_all_chatterbox(["Original sentence."], "english", Path(tmp))
                        voice._synth_all_chatterbox(["Changed sentence."], "english", Path(tmp))

            self.assertEqual(calls, ["Original sentence.", "Changed sentence."])

    def test_corrupt_cached_audio_is_regenerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"

            def fake_synth(text, language, path):
                path.write_bytes(b"RIFF" + b"0" * 2048)

            with patch.dict(os.environ, {"CHATTERBOX_CACHE_DIR": str(cache)}):
                with patch.object(voice, "_synth_chatterbox", side_effect=fake_synth) as synth:
                    with patch.object(voice, "_dur", return_value=2.5):
                        paths = voice._synth_all_chatterbox(["A sentence."], "english", Path(tmp))
                        paths[0].write_bytes(b"corrupt")
                        voice._synth_all_chatterbox(["A sentence."], "english", Path(tmp))

            self.assertEqual(synth.call_count, 2)


if __name__ == "__main__":
    unittest.main()
