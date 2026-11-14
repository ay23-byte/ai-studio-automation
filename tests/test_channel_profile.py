import os
import unittest
from unittest.mock import patch

from core.channel_profile import apply_configured_channel_profile, load_channel_profile


class ChannelProfileTests(unittest.TestCase):
    def test_standard_profile_is_noop(self):
        with patch.dict(os.environ, {"CHANNEL_PROFILE": "standard"}, clear=False):
            self.assertEqual(load_channel_profile(), {})

    def test_beyond_science_profile_loads_existing_channel_identity(self):
        profile = load_channel_profile("beyond_science")
        self.assertEqual(profile["channel_name"], "Engineering Physics")
        self.assertIn("science", profile["audience"].lower())
        self.assertIn("original wording", profile["brand_voice"].lower())
        self.assertIn("geopolitics", profile["brand_voice"].lower())

    def test_profile_is_injected_only_when_selected(self):
        with patch.dict(os.environ, {"CHANNEL_PROFILE": "beyond_science"}, clear=False):
            channel, audience, voice = apply_configured_channel_profile()
        self.assertEqual(channel, "Engineering Physics")
        self.assertTrue(audience)
        self.assertIn("Beyond Science", voice)

    def test_explicit_arguments_override_profile_defaults(self):
        with patch.dict(os.environ, {"CHANNEL_PROFILE": "beyond_science"}, clear=False):
            channel, audience, voice = apply_configured_channel_profile(
                "Custom Channel", "Custom audience", "Custom voice")
        self.assertEqual((channel, audience, voice), ("Custom Channel", "Custom audience", "Custom voice"))

    def test_unknown_profile_fails_clearly(self):
        with self.assertRaises(ValueError):
            load_channel_profile("mystery_profile")


if __name__ == "__main__":
    unittest.main()
