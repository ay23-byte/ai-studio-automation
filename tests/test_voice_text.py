import sys
import types
import unittest

# Sentence-splitting tests do not need the Edge TTS network client.
sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))
from core.voice import _sentences, _speech_chunks


class VoiceTextTests(unittest.TestCase):
    def test_splits_hindi_and_english_sentence_boundaries(self):
        text = "पहला वाक्य। दूसरा वाक्य! Third sentence. Fourth?"
        self.assertEqual(
            _sentences(text),
            ["पहला वाक्य।", "दूसरा वाक्य!", "Third sentence.", "Fourth?"],
        )

    def test_collapses_extra_whitespace(self):
        self.assertEqual(_sentences("पहला।   दूसरा।\nतीसरा।"),
                         ["पहला।", "दूसरा।", "तीसरा।"])

    def test_speech_chunks_respect_maximum_length(self):
        chunks = _speech_chunks("एक। दूसरा। तीसरा।", max_chars=8)
        self.assertEqual(chunks, ["एक।", "दूसरा।", "तीसरा।"])

    def test_pause_length_matches_spoken_punctuation(self):
        from core.voice import _pause_after
        self.assertGreater(_pause_after("Really?"), _pause_after("A short phrase,"))
        self.assertGreater(_pause_after("A sentence।"), _pause_after("A short phrase"))

    def test_pronunciation_map_replaces_whole_words_case_insensitively(self):
        from core.voice import _apply_pronunciation_map
        self.assertEqual(
            _apply_pronunciation_map("OpenAI helps AI.", {"OpenAI": "Open A I"}),
            "Open A I helps AI.",
        )

    def test_pronunciation_map_does_not_replace_inside_words(self):
        from core.voice import _apply_pronunciation_map
        self.assertEqual(
            _apply_pronunciation_map("AIndia and India", {"India": "In-dee-ah"}),
            "AIndia and In-dee-ah",
        )


if __name__ == "__main__":
    unittest.main()
