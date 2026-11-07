import json
import tempfile
import unittest
from pathlib import Path

from scripts.render_reviewed_news_short import find_artifact_file, validate_inputs


class RenderReviewedNewsShortTests(unittest.TestCase):
    def setUp(self):
        self.packet = {
            "review_status": "approved",
            "sources": [{"source_id": "S2"}],
            "verified_facts": [{"fact": "A verified result."}],
        }
        self.script = {
            "title": "Himalayan flood exposure study",
            "editorial_status": "draft_requires_human_script_and_visual_review",
            "scenes": [
                {"say": f"नरेशन {i}", "caption": f"Caption {i}", "image": f"Visual {i}",
                 "source_ids": ["S2"]}
                for i in range(4)
            ],
        }

    def test_accepts_approved_evidence_and_reviewed_script(self):
        validate_inputs(self.packet, self.script)

    def test_rejects_unapproved_packet(self):
        packet = dict(self.packet, review_status="pending")
        with self.assertRaisesRegex(ValueError, "not approved"):
            validate_inputs(packet, self.script)

    def test_rejects_script_without_human_review_status(self):
        script = dict(self.script, editorial_status="approved_for_publication")
        with self.assertRaisesRegex(ValueError, "human-review draft"):
            validate_inputs(self.packet, script)

    def test_rejects_scene_without_source_reference(self):
        script = dict(self.script)
        script["scenes"] = [dict(scene) for scene in self.script["scenes"]]
        script["scenes"][0]["source_ids"] = []
        with self.assertRaisesRegex(ValueError, "no evidence source"):
            validate_inputs(self.packet, script)

    def test_rejects_unknown_source_reference(self):
        script = dict(self.script)
        script["scenes"] = [dict(scene) for scene in self.script["scenes"]]
        script["scenes"][0]["source_ids"] = ["S99"]
        with self.assertRaisesRegex(ValueError, "unknown evidence source"):
            validate_inputs(self.packet, script)

    def test_requires_four_to_six_scenes(self):
        script = dict(self.script, scenes=self.script["scenes"][:3])
        with self.assertRaisesRegex(ValueError, "4–6 scenes"):
            validate_inputs(self.packet, script)

    def test_finds_nested_artifact_file(self):
        with tempfile.TemporaryDirectory() as temp:
            nested = Path(temp) / "nested" / "daily_news"
            nested.mkdir(parents=True)
            target = nested / "reviewed_story.json"
            target.write_text(json.dumps(self.packet), encoding="utf-8")
            self.assertEqual(find_artifact_file(temp, "reviewed_story.json"), target)

    def test_rejects_missing_artifact_file(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "found 0"):
                find_artifact_file(temp, "reviewed_story.json")


if __name__ == "__main__":
    unittest.main()
