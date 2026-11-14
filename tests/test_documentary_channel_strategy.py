import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentaryChannelStrategyTests(unittest.TestCase):
    def test_channel_strategy_preserves_existing_channel_and_hinglish(self):
        data = json.loads((ROOT / "config/documentary_channel_strategy.json").read_text(encoding="utf-8"))
        self.assertEqual(data["channel_name"], "Engineering Physics")
        self.assertEqual(data["channel_handle"], "@engineeringphysics-n5g")
        self.assertEqual(data["language"]["primary"], "hinglish")
        self.assertEqual(data["language"]["subtitles"], "English")
        self.assertEqual(data["strategy_type"], "existing_channel_pilot")

    def test_longform_and_shorts_targets_are_valid(self):
        data = json.loads((ROOT / "config/documentary_channel_strategy.json").read_text(encoding="utf-8"))
        longform = data["formats"]["longform"]
        shorts = data["formats"]["shorts"]
        self.assertIn(longform["target_minutes"], longform["allowed_minutes"])
        self.assertEqual(longform["minimum_resolution"], "1920x1080")
        self.assertEqual(shorts["aspect_ratio"], "9:16")
        self.assertEqual(shorts["target_seconds"], [35, 58])

    def test_documentary_topic_bank_has_unique_proposals(self):
        data = json.loads((ROOT / "config/documentary_topic_bank.json").read_text(encoding="utf-8"))
        topics = data["topics"]
        self.assertGreaterEqual(len(topics), 10)
        self.assertEqual(len({row["id"] for row in topics}), len(topics))
        self.assertTrue(all(row["status"] == "proposed" for row in topics))
        self.assertTrue(all(row["format"] == "longform" for row in topics))


if __name__ == "__main__":
    unittest.main()
