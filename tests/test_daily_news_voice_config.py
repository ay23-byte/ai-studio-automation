import unittest
from pathlib import Path


class DailyNewsVoiceConfigurationTests(unittest.TestCase):
    def test_daily_news_uses_chatterbox_for_hinglish_and_edge_for_explicit_languages(self):
        workflow = Path(".github/workflows/daily-news-auto-short.yml").read_text(encoding="utf-8")
        self.assertIn("inputs.language || 'hinglish'", workflow)
        self.assertIn("&& 'chatterbox' || 'edge'", workflow)
        self.assertIn("TTS_REQUIRE_ENGINE:", workflow)
        self.assertIn('ALLOW_CHATTERBOX_ON_CI: "true"', workflow)
        self.assertIn("hi-IN-MadhurNeural", workflow)
        self.assertIn("en-IN-PrabhatNeural", workflow)
        self.assertIn("Install Chatterbox multilingual for Hinglish narration", workflow)

    def test_voice_audition_generates_real_samples_for_all_pipelines(self):
        script = Path("scripts/create_pipeline_voice_audition.py").read_text(encoding="utf-8")
        for pipeline in ("general_shorts", "daily_news", "documentary"):
            self.assertIn(pipeline, script)
        self.assertIn("inspect_narration", script)
        self.assertIn("human_listening_review", script)


if __name__ == "__main__":
    unittest.main()
