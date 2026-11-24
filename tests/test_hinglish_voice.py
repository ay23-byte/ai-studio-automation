import unittest

from core import voice


class HinglishChatterboxLanguageTests(unittest.TestCase):
    def test_hindi_dominant_hinglish_phrase_uses_hindi_model(self):
        self.assertEqual(
            voice._chatterbox_language_id("आज हम इस technology को समझेंगे", "hinglish"),
            "hi",
        )

    def test_english_dominant_phrase_uses_english_model(self):
        self.assertEqual(
            voice._chatterbox_language_id("This technology changes the energy system", "hinglish"),
            "en",
        )

    def test_explicit_language_overrides_script_detection(self):
        self.assertEqual(voice._chatterbox_language_id("This is English", "hindi"), "hi")
        self.assertEqual(voice._chatterbox_language_id("यह हिंदी है", "english"), "en")

    def test_phrase_cache_fingerprint_tracks_actual_language(self):
        hindi = voice._chatterbox_phrase_signature("आज हम technology समझेंगे", "hinglish")
        english = voice._chatterbox_phrase_signature("Today we study technology", "hinglish")
        self.assertNotEqual(hindi, english)


if __name__ == "__main__":
    unittest.main()
