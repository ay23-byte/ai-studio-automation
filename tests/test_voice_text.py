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


if __name__ == "__main__":
    unittest.main()
