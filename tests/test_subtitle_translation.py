import unittest
from unittest.mock import patch

from core.subtitle_translation import translate_longform_subtitles


class EnglishDocumentarySubtitleTests(unittest.TestCase):
    @patch("core.subtitle_translation._ask_gemini")
    def test_translates_hindi_captions_and_titles_without_changing_timing(self, ask):
        ask.return_value = (
            '{"translations":['
            '{"id":0,"text":"The village is disappearing."},'
            '{"id":1,"text":"A changing landscape"}]}'
        )
        timings = [(1.25, 3.5, "यह गाँव धीरे-धीरे गायब हो रहा है।")]
        sections = [{"title": "बदलता हुआ परिदृश्य"}]
        translated, titles = translate_longform_subtitles(timings, sections)
        self.assertEqual(translated, [(1.25, 3.5, "The village is disappearing.")])
        self.assertEqual(titles, ["A changing landscape"])

    @patch("core.subtitle_translation._ask_gemini")
    def test_rejects_translation_that_still_contains_devanagari(self, ask):
        ask.return_value = '{"translations":[{"id":0,"text":"यह is still Hindi"}]}'
        with self.assertRaisesRegex(RuntimeError, "still contains Devanagari"):
            translate_longform_subtitles([(0, 2, "यह")], [])

    @patch("core.subtitle_translation._ask_gemini")
    def test_rejects_missing_translation_lines(self, ask):
        ask.return_value = '{"translations":[]}'
        with self.assertRaisesRegex(RuntimeError, "different number"):
            translate_longform_subtitles([(0, 2, "नमस्ते")], [])

    @patch("core.subtitle_translation._ask_gemini")
    def test_batches_large_subtitle_files(self, ask):
        ask.side_effect = [
            '{"translations":[' + ",".join(
                f'{{"id":{i},"text":"English line {i}"}}' for i in range(35)
            ) + ']}',
            '{"translations":[{"id":35,"text":"English line 35"}]}',
        ]
        timings = [(float(i), float(i + 1), f"लाइन {i}") for i in range(36)]
        translated, _ = translate_longform_subtitles(timings, [])
        self.assertEqual(len(translated), 36)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(translated[-1][2], "English line 35")


if __name__ == "__main__":
    unittest.main()
