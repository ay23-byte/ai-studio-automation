import os
import unittest
from unittest.mock import patch

from core.voice import _pause_after, _prosody


class DocumentaryVoicePacingTests(unittest.TestCase):
    def test_sentence_pauses_are_longer_than_clause_pauses(self):
        self.assertGreater(_pause_after("A full sentence."), _pause_after("A short clause,"))
        self.assertGreater(_pause_after("A question?"), _pause_after("A full sentence."))

    def test_default_narration_rate_is_deliberate_but_not_extreme(self):
        with patch.dict(os.environ, {}, clear=True):
            rate, pitch = _prosody(1, 4, "A documentary sentence.")
        self.assertEqual(rate, "+3%")
        self.assertEqual(pitch, "+0Hz")

    def test_pause_lengths_are_tighter_for_a_professional_read(self):
        self.assertAlmostEqual(_pause_after("A sentence."), 0.26)
        self.assertAlmostEqual(_pause_after("A clause,"), 0.18)
        self.assertLess(_pause_after("A sentence."), 0.50)

    def test_longform_pause_scale_adds_breathing_room(self):
        with patch.dict(os.environ, {"TTS_PAUSE_SCALE": "1.4"}):
            self.assertAlmostEqual(_pause_after("A full sentence."), 0.364)
            self.assertAlmostEqual(_pause_after("A clause,"), 0.252)

    def test_opening_and_final_lines_receive_clearer_delivery(self):
        with patch.dict(os.environ, {}, clear=True):
            opening = _prosody(0, 3, "Opening line.")
            closing = _prosody(2, 3, "Final line.")
        self.assertEqual(opening[0], "+2%")
        self.assertEqual(closing[0], "+3%")


if __name__ == "__main__":
    unittest.main()
