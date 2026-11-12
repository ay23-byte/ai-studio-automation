import unittest

from core.voice import pick_voice, _edge_voice_candidates


class NewsLanguageVoiceTests(unittest.TestCase):
    def test_hinglish_preserves_existing_indian_hindi_voice_default(self):
        self.assertEqual(pick_voice("hinglish", "female"), pick_voice("hindi", "female"))

    def test_english_voice_selection_remains_distinct(self):
        self.assertEqual(pick_voice("english", "female"), "en-IN-NeerjaNeural")

    def test_hinglish_edge_fallbacks_preserve_hindi_voice_chain(self):
        candidates = _edge_voice_candidates("hinglish", "female")
        self.assertIn("hi-IN-SwaraNeural", candidates)
        self.assertIn("hi-IN-AnanyaNeural", candidates)


if __name__ == "__main__":
    unittest.main()
