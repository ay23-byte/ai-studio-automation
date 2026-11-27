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

    def test_any_devanagari_selects_hindi_even_when_latin_terms_dominate(self):
        phrase = "Quantum sensors और gravitational waves की ultra precise measurement"
        self.assertEqual(voice._chatterbox_language_id(phrase, "hinglish"), "hi")

    def test_hinglish_chunks_split_language_switches_at_sentence_boundaries(self):
        text = (
            "आज हम quantum sensors समझेंगे। "
            "They measure tiny changes in physical systems. "
            "फिर हम इसका practical example देखेंगे।"
        )
        chunks = voice._speech_chunks(text, language="hinglish")
        self.assertEqual(
            [voice._chatterbox_language_id(chunk, "hinglish") for chunk in chunks],
            ["hi", "en", "hi"],
        )
        self.assertEqual(len(chunks), 3)

    def test_mixed_sentence_is_not_split_mid_sentence(self):
        text = "जब quantum sensor काम करता है, तो tiny signals detect होते हैं।"
        chunks = voice._speech_chunks(text, language="hinglish")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(voice._chatterbox_language_id(chunks[0], "hinglish"), "hi")

    def test_explicit_language_overrides_script_detection(self):
        self.assertEqual(voice._chatterbox_language_id("This is English", "hindi"), "hi")
        self.assertEqual(voice._chatterbox_language_id("यह हिंदी है", "english"), "en")

    def test_phrase_cache_fingerprint_tracks_actual_language(self):
        hindi = voice._chatterbox_phrase_signature("आज हम technology समझेंगे", "hinglish")
        english = voice._chatterbox_phrase_signature("Today we study technology", "hinglish")
        self.assertNotEqual(hindi, english)


if __name__ == "__main__":
    unittest.main()
