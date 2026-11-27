import unittest
from pathlib import Path


class DailyNewsVoiceConfigurationTests(unittest.TestCase):
    def test_daily_news_uses_selected_neural_indian_voice_not_unconditioned_chatterbox(self):
        workflow = Path(".github/workflows/daily-news-auto-short.yml").read_text(encoding="utf-8")
        self.assertIn('TTS_ENGINE: "edge"', workflow)
        self.assertIn('TTS_REQUIRE_ENGINE: "edge"', workflow)
        self.assertIn("en-IN-PrabhatNeural", workflow)
        self.assertIn("hi-IN-MadhurNeural", workflow)
        self.assertNotIn('TTS_ENGINE: "chatterbox"', workflow)
        self.assertNotIn("Install Chatterbox multilingual narration", workflow)

    def test_news_delivery_uses_less_rushed_rate_and_longer_phrase_pauses(self):
        workflow = Path(".github/workflows/daily-news-auto-short.yml").read_text(encoding="utf-8")
        self.assertIn('TTS_RATE: "-2%"', workflow)
        self.assertIn('TTS_OPENING_RATE: "-3%"', workflow)
        self.assertIn('TTS_CLOSING_RATE: "-2%"', workflow)
        self.assertIn('TTS_PAUSE_SCALE: "1.15"', workflow)
        self.assertIn('TTS_REJECT_ROBOTIC_FALLBACK: "1"', workflow)


if __name__ == "__main__":
    unittest.main()
