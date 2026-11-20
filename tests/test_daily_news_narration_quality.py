"""Regression tests for clear, brisk Daily News Shorts narration."""
import os
import unittest
from unittest.mock import patch

from core import news_auto, voice


class NewsNarrationEditorialTests(unittest.TestCase):
    def setUp(self):
        self.story = {
            "headline": "New satellite mission expands climate monitoring",
            "sources": [
                {"publisher": "Example News", "title": "New satellite mission expands climate monitoring",
                 "text_snippet": "The satellite will monitor ocean temperatures and atmospheric changes."},
                {"publisher": "World Journal", "title": "Satellite mission adds climate data",
                 "text_snippet": "The mission will collect additional climate observations."},
            ],
        }

    def test_template_does_not_read_publisher_or_website_names_aloud(self):
        script = news_auto.template_script(self.story)
        spoken = " ".join(scene["say"] for scene in script["scenes"])
        self.assertNotIn("Example News", spoken)
        self.assertNotIn("World Journal", spoken)
        self.assertIn("according to corroborating reports", spoken.lower())
        self.assertEqual(len(script["scenes"]), 5)

    def test_validator_rejects_a_spoken_website_domain(self):
        script = news_auto.template_script(self.story)
        script["scenes"][0]["say"] += " Visit example.com for details."
        problems = news_auto.validate_news_script(script, self.story, "english")
        self.assertTrue(any("URL or website domain" in issue for issue in problems), problems)

    def test_fast_news_profile_tightens_sentence_pauses(self):
        with patch.dict(os.environ, {"TTS_PAUSE_SCALE": "0.55"}):
            self.assertEqual(voice._pause_after("A sentence."), 0.143)
            self.assertEqual(voice._pause_after("A clause,"), 0.099)

    def test_news_voice_override_is_respected(self):
        with patch.dict(os.environ, {"TTS_VOICE": "en-US-AndrewNeural"}):
            self.assertEqual(voice.pick_voice("english", "male"), "en-US-AndrewNeural")

    def test_all_report_sources_are_at_the_end_of_youtube_description(self):
        sources = [
            {"publisher": "Example News", "title": "First report",
             "url": "https://example.com/first"},
            {"publisher": "World Journal", "title": "Independent report",
             "url": "https://world.example.org/story"},
            {"publisher": "Example News", "title": "Duplicate link",
             "url": "https://example.com/first"},
        ]
        description = news_auto._build_news_description("News headline", sources)
        self.assertIn("Sources & Further Reading", description)
        self.assertIn("Example News — First report", description)
        self.assertIn("https://example.com/first", description)
        self.assertIn("World Journal — Independent report", description)
        self.assertIn("https://world.example.org/story", description)
        self.assertEqual(description.count("https://example.com/first"), 1)
        self.assertGreater(description.index("Sources & Further Reading"), description.index("A sourced news summary"))
        self.assertTrue(description.rstrip().endswith("https://world.example.org/story"))


if __name__ == "__main__":
    unittest.main()
