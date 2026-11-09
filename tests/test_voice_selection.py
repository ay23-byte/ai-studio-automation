import os
import unittest
from unittest.mock import patch

from core.voice import pick_voice, _pause_after


class VoiceSelectionTests(unittest.TestCase):
    def test_explicit_voice_override_wins_for_hinglish(self):
        with patch.dict(os.environ, {"TTS_VOICE": "hi-IN-SwaraNeural"}):
            self.assertEqual(pick_voice("hinglish", "female"), "hi-IN-SwaraNeural")

    def test_english_female_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pick_voice("english", "female"), "en-IN-NeerjaNeural")

    def test_hindi_female_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pick_voice("hinglish", "female"), "hi-IN-SwaraNeural")

    def test_pause_policy_respects_punctuation(self):
        self.assertGreater(_pause_after("Really?"), _pause_after("This happened."))
        self.assertGreater(_pause_after("This happened."), _pause_after("However,"))


if __name__ == "__main__":
    unittest.main()
