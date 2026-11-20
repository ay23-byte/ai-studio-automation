import unittest
from unittest.mock import patch

from core.subtitle_translation import shorts_subtitle_language, translate_short_subtitles


class EnglishShortSubtitleTests(unittest.TestCase):
    @patch("core.subtitle_translation._ask_gemini")
    def test_translates_captions_labels_hook_and_cta_without_changing_timing(self, ask):
        ask.return_value = (
            '{"translations":['
            '{"id":0,"text":"The river is changing."},'
            '{"id":1,"text":"A changing landscape"},'
            '{"id":2,"text":"Why is this happening?"},'
            '{"id":3,"text":"Follow for more science"}]}'
        )
        captions = [(1.25, 3.5, "नदी बदल रही है।")]
        labels = [(0.0, 1.25, "बदलता परिदृश्य")]
        result = translate_short_subtitles(
            captions, labels, "ऐसा क्यों हो रहा है?", "और जानने के लिए फॉलो करें",
            source_language="hinglish")
        translated, translated_labels, hook, cta, report = result
        self.assertEqual(translated, [(1.25, 3.5, "The river is changing.")])
        self.assertEqual(translated_labels, [(0.0, 1.25, "A changing landscape")])
        self.assertEqual(hook, "Why is this happening?")
        self.assertEqual(cta, "Follow for more science")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["mode"], "translated")

    @patch("core.subtitle_translation._ask_gemini")
    def test_english_narration_does_not_call_translation_provider(self, ask):
        captions, labels, hook, cta, report = translate_short_subtitles(
            [(0, 2, "The experiment begins.")], [], "Watch closely", "Follow for more",
            source_language="english")
        ask.assert_not_called()
        self.assertEqual(captions[0][2], "The experiment begins.")
        self.assertEqual(report["mode"], "already_target_language")

    @patch("core.subtitle_translation._ask_gemini")
    def test_rejects_latin_script_hinglish_left_in_english_subtitles(self, ask):
        ask.return_value = '{"translations":[{"id":0,"text":"Yeh gaon ab dheere dheere badal raha hai aur log"}]}'
        with self.assertRaisesRegex(RuntimeError, "not English"):
            translate_short_subtitles([(0, 2, "यह गाँव बदल रहा है")], [], source_language="hindi")

    def test_channel_shorts_default_to_english_subtitles(self):
        self.assertEqual(shorts_subtitle_language(), "english")

    def test_non_english_subtitles_are_blocked_by_default_policy(self):
        with self.assertRaisesRegex(ValueError, "English only"):
            shorts_subtitle_language("hinglish")


if __name__ == "__main__":
    unittest.main()
