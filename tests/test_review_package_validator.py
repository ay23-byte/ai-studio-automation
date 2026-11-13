import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validate_review_package import validate_package


class ReviewPackageValidatorTests(unittest.TestCase):
    def make_package(self, root, mode="shortform"):
        (root / "video.mp4").write_bytes(b"x" * 2048)
        (root / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
        (root / "asset.jpg").write_bytes(b"image")
        (root / "qa").mkdir()
        (root / "preview" / "frames").mkdir(parents=True)
        (root / "preview" / "frames" / "frame.jpg").write_bytes(b"frame")
        (root / "review_checklist.md").write_text("# Review\n", encoding="utf-8")
        manifest = {
            "schema_version": 1, "mode": mode, "topic": "Test topic",
            "video_file": "video.mp4", "captions_file": "captions.srt",
            "scenes": [{"scene_id": "scene-1", "narration": "Test narration.",
                "assets": [{"path": "asset.jpg", "asset_type": "image",
                    "provenance": {"origin": "ai_generated", "prompt": "A test visual"},
                    "rights": {"status": "reviewed", "license": "AI generated"}}]}]
        }
        (root / "production_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        for name in ("technical", "semantic", "audio", "captions", "assets"):
            (root / "qa" / f"{name}.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        checklist = {field: "approved" for field in
                     ("editorial", "factual", "visual", "audio", "rights", "disclosure")}
        checklist["human_review_status"] = "approved"
        (root / "review_checklist.json").write_text(json.dumps(checklist), encoding="utf-8")

    @patch("scripts.validate_review_package._probe", return_value={
        "width": 1080, "height": 1920, "duration_seconds": 40,
        "video_codec": "h264", "audio_codecs": ["aac"]})
    def test_schema_v1_package_is_never_approved_even_with_a_complete_checklist(self, _probe):
        # A v1 checklist names no job, no video hash and no reviewer, so a fully
        # "approved" v1 package still cannot be released.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_package(root)
            result = validate_package(root)
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertFalse(result["publication_eligible"])
            self.assertIn("schema v1 package", " ".join(result["required_human_reviews"]))

    @patch("scripts.validate_review_package._probe", return_value={
        "width": 1920, "height": 1080, "duration_seconds": 60,
        "video_codec": "h264", "audio_codecs": ["aac"]})
    def test_wrong_aspect_ratio_fails(self, _probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_package(root)
            result = validate_package(root)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["publication_eligible"])

    @patch("scripts.validate_review_package._probe", side_effect=ValueError("ffprobe could not decode"))
    def test_invalid_mp4_fails_closed(self, _probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_package(root)
            result = validate_package(root)
            self.assertEqual(result["status"], "FAIL")

    @patch("scripts.validate_review_package._probe", return_value={
        "width": 1080, "height": 1920, "duration_seconds": 40,
        "video_codec": "h264", "audio_codecs": ["aac"]})
    def test_unknown_rights_and_missing_human_review_require_review(self, _probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_package(root)
            checklist = json.loads((root / "review_checklist.json").read_text())
            checklist["rights"] = "pending"
            (root / "review_checklist.json").write_text(json.dumps(checklist))
            manifest = json.loads((root / "production_manifest.json").read_text())
            manifest["scenes"][0]["assets"][0]["rights"] = {"status": "unknown", "license": None}
            (root / "production_manifest.json").write_text(json.dumps(manifest))
            result = validate_package(root)
            self.assertEqual(result["status"], "REVIEW_REQUIRED")
            self.assertFalse(result["publication_eligible"])

    @patch("scripts.validate_review_package._probe", return_value={
        "width": 1080, "height": 1920, "duration_seconds": 40,
        "video_codec": "h264", "audio_codecs": ["aac"]})
    def test_path_traversal_fails(self, _probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_package(root)
            manifest = json.loads((root / "production_manifest.json").read_text())
            manifest["scenes"][0]["assets"][0]["path"] = "../outside.jpg"
            (root / "production_manifest.json").write_text(json.dumps(manifest))
            result = validate_package(root)
            self.assertEqual(result["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
