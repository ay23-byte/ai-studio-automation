"""Documentary subtitles are English only, whatever language the narration is in."""
import os
import unittest
from unittest.mock import patch

from core.subtitle_translation import documentary_subtitle_language, english_subtitle_report

ENGLISH = [
    "In 1942, the first reactor ran for four and a half minutes.",
    "The control rods absorb neutrons, so the chain reaction slows.",
    "Fermi called it a pile, because that is what it was.",
    "Half a watt: not enough to light a bulb.",
    "The same physics now drives four hundred power reactors.",
]
HINGLISH = [
    "1942 mein pehla reactor sirf saadhe chaar minute chala tha.",
    "Control rods neutrons ko absorb karte hain, isliye reaction dheemi hoti hai.",
    "Fermi ne ise pile kaha, kyunki yeh wahi tha.",
    "Aadha watt: ek bulb jalane ke liye bhi kaafi nahi hai.",
    "Yahi physics aaj chaar sau reactors ko chalati hai.",
]
HINDI = ["1942 में पहला रिएक्टर साढ़े चार मिनट चला।", "नियंत्रण छड़ें न्यूट्रॉन सोख लेती हैं।"]


class EnglishSubtitleReportTests(unittest.TestCase):
    def test_english_passes(self):
        report = english_subtitle_report(ENGLISH)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["devanagari_lines"], 0)

    def test_devanagari_fails(self):
        report = english_subtitle_report(ENGLISH + HINDI)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["devanagari_lines"], 2)
        self.assertIn("Devanagari", report["issues"][0])

    def test_hindi_in_latin_letters_fails(self):
        report = english_subtitle_report(HINGLISH)
        self.assertEqual(report["status"], "FAIL")
        self.assertGreaterEqual(report["roman_hindi_lines"], 4)
        self.assertIn("Latin letters", report["issues"][0])

    def test_english_with_names_and_one_borrowed_word_still_passes(self):
        lines = ENGLISH * 4 + ["The Bhabha Atomic Research Centre is in Trombay.", "Homi Bhabha led the programme."]
        self.assertEqual(english_subtitle_report(lines)["status"], "PASS")

    def test_a_few_hinglish_lines_inside_an_english_file_are_caught(self):
        report = english_subtitle_report(ENGLISH * 6 + HINGLISH[:3])
        self.assertEqual(report["status"], "FAIL", report)

    def test_empty_input_passes_without_dividing_by_zero(self):
        self.assertEqual(english_subtitle_report([])["status"], "PASS")


class DocumentarySubtitlePolicyTests(unittest.TestCase):
    def test_english_is_the_default_and_is_accepted(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOCUMENTARY_SUBTITLE_POLICY", None)
            self.assertEqual(documentary_subtitle_language(None), "english")
            self.assertEqual(documentary_subtitle_language("EN"), "english")

    def test_hindi_hinglish_and_as_spoken_subtitles_are_refused(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOCUMENTARY_SUBTITLE_POLICY", None)
            for requested in ("hindi", "hinglish", "source", "hi", "spoken"):
                with self.assertRaises(ValueError) as caught:
                    documentary_subtitle_language(requested)
                self.assertIn("English only", str(caught.exception))

    def test_the_policy_can_be_lifted_explicitly(self):
        self.assertEqual(documentary_subtitle_language("hindi", policy="any"), "hindi")

    def test_the_documentary_workflow_states_english_subtitles(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parent.parent / ".github/workflows/make_longform.yml").read_text(encoding="utf-8")
        self.assertIn('SUBTITLE_LANGUAGE: "english"', workflow)
        self.assertNotIn("SUBTITLE_TRANSLATION_FALLBACK", workflow)
        self.assertNotIn("DOCUMENTARY_SUBTITLE_POLICY", workflow)


if __name__ == "__main__":
    unittest.main()
