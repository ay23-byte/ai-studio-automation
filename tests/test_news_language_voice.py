import unittest

from core.voice import pick_voice, _edge_voice_candidates


class NewsLanguageVoiceTests(unittest.TestCase):
    def test_hinglish_uses_an_english_capable_configured_voice(self):
        self.assertEqual(pick_voice("hinglish", "male"), pick_voice("english", "male"))

    def test_hinglish_fallbacks_are_english_voices(self):
        candidates = _edge_voice_candidates("hinglish", "male")
        self.assertIn("en-IN-PrabhatNeural", candidates)
        self.assertIn("en-US-AriaNeural", candidates)
        self.assertNotIn("hi-IN-MadhurNeural", candidates)


if __name__ == "__main__":
    unittest.main()
