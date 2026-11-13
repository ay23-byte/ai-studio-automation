import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_review_decisions import apply_decisions
from scripts.release_review_package import release_package


class HumanDecisionTests(unittest.TestCase):
    def setup_package(self, root):
        (root / "qa").mkdir()
        (root / "assets").mkdir()
        (root / "preview" / "frames").mkdir(parents=True)
        (root / "video.mp4").write_bytes(b"video")
        (root / "captions.srt").write_text("caption")
        (root / "assets" / "a.jpg").write_bytes(b"img")
        manifest = {
            "schema_version": 1, "mode": "shortform", "topic": "T",
            "video_file": "video.mp4", "captions_file": "captions.srt",
            "scenes": [{
                "scene_id": "s1", "narration": "voice",
                "assets": [{
                    "path": "assets/a.jpg", "asset_type": "image",
                    "provenance": {"origin": "source", "source_url": "https://example.org/a"},
                    "rights": {"status": "unknown"}
                }]
            }]
        }
        (root / "production_manifest.json").write_text(json.dumps(manifest))
        for name in ("technical", "semantic", "audio", "captions", "assets"):
            status = "PASS" if name == "technical" else "REVIEW"
            (root / "qa" / (name + ".json")).write_text(json.dumps({"status": status}))
        decisions = {
            "schema_version": 1, "reviewer": "Reviewer A",
            "reviewed_at": "2026-11-13T12:00:00Z",
            "confirmations": {key: "approved" for key in
                              ("editorial", "factual", "visual", "audio", "rights", "disclosure")},
            "scene_reviews": [{
                "scene_id": "s1", "status": "approved",
                "notes": "Inspected the complete shot against its narration."
            }],
            "asset_reviews": [{
                "scene_id": "s1", "path": "assets/a.jpg", "status": "reviewed",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "attribution": "Creator and source title",
                "notes": "Checked original item page and exact licence conditions."
            }],
            "qa_reviews": {
                key: {"status": "PASS", "notes": "Reviewed report and corresponding media evidence."}
                for key in ("semantic", "audio", "captions", "assets")
            }
        }
        (root / "decisions.json").write_text(json.dumps(decisions))
        return manifest

    def test_applies_human_reviews_and_rights(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_package(root)
            result = apply_decisions(root, root / "decisions.json")
            self.assertEqual(result["scene_count"], 1)
            checklist = json.loads((root / "review_checklist.json").read_text())
            self.assertEqual(checklist["human_review_status"], "approved")

    def test_unknown_provenance_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.setup_package(root)
            manifest["scenes"][0]["assets"][0]["provenance"] = {"origin": "unknown"}
            (root / "production_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "unknown provenance"):
                apply_decisions(root, root / "decisions.json")

    def test_failed_qa_never_overridden(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_package(root)
            (root / "qa" / "semantic.json").write_text('{"status":"FAIL"}')
            with self.assertRaisesRegex(ValueError, "cannot be overridden"):
                apply_decisions(root, root / "decisions.json")

    def test_documentary_mode_is_not_accepted_by_shorts_releaser(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self.setup_package(root)
            manifest["mode"] = "longform"
            (root / "production_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "only accepts shortform or daily_news"):
                release_package(root, root / "decisions.json", "0" * 64, True)

    def test_requires_explicit_private_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.setup_package(root)
            with self.assertRaisesRegex(ValueError, "confirm_private_upload"):
                release_package(root, root / "decisions.json", "0" * 64)


if __name__ == "__main__":
    unittest.main()
