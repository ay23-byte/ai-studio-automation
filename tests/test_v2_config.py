"""Milestone 2: safe configuration defaults."""
from __future__ import annotations

import unittest

from src.production_v2.config import CHANNEL_HANDLE, PRIVACY_STATUS, V2Config


class ConfigTests(unittest.TestCase):
    def test_defaults_are_opt_in_and_private(self):
        config = V2Config.from_env({})
        self.assertFalse(config.enabled)
        self.assertFalse(config.upload_enabled)
        self.assertEqual(config.provider, "mock")
        self.assertEqual(config.language, "english")
        self.assertEqual(config.subtitle_language, "english")
        self.assertEqual(PRIVACY_STATUS, "private")
        self.assertEqual(CHANNEL_HANDLE, "@engineeringphysics-n5g")
        config.assert_safe()

    def test_public_privacy_or_unlisted_is_impossible(self):
        config = V2Config.from_env({"PRIVACY": "public"})
        config.assert_safe()
        self.assertEqual(PRIVACY_STATUS, "private")

    def test_paid_provider_is_rejected(self):
        config = V2Config.from_env({"PV2_PROVIDER": "paid-api"})
        with self.assertRaises(ValueError):
            config.assert_safe()

    def test_non_english_subtitles_rejected_for_english_pipeline(self):
        config = V2Config.from_env({"PV2_SUBTITLE_LANGUAGE": "hindi"})
        with self.assertRaises(ValueError):
            config.assert_safe()

    def test_content_rules_cover_all_three_pipelines(self):
        config = V2Config.from_env({})
        self.assertEqual(config.rules_for("documentary").aspect, "16:9")
        self.assertEqual(config.rules_for("news_short").aspect, "9:16")
        self.assertEqual(config.rules_for("physics_short").aspect, "9:16")


if __name__ == "__main__":
    unittest.main()
