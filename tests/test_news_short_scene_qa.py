import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.news_short_scene_qa import audit_scene_footage


class SceneFootageQATests(unittest.TestCase):
    def _manifest(self, root, shots=None):
        video = root / "review_short.mp4"
        video.write_bytes(b"video")
        manifest = root / "production_manifest.json"
        manifest.write_text(json.dumps({"shots": ([
            {"start_seconds": i * 5, "duration_seconds": 5} for i in range(4)
        ] if shots is None else shots)}), encoding="utf-8")
        return manifest, video

    @patch("core.news_short_scene_qa._probe_duration", return_value=20.0)
    def test_missing_manifest_scenes_fail_closed(self, probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, video = self._manifest(root, shots=[])
            result = audit_scene_footage(manifest, video)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["publication_ready"])

    @patch("core.news_short_scene_qa._frame_difference", return_value=8.0)
    @patch("core.news_short_scene_qa._frame_metrics", return_value={"mean_luma": 90, "pixel_stddev": 30})
    @patch("core.news_short_scene_qa._extract_frame", return_value=True)
    @patch("core.news_short_scene_qa._probe_duration", return_value=20.0)
    def test_all_scenes_sampled_and_verified(self, probe, extract, metrics, difference):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, video = self._manifest(root)
            result = audit_scene_footage(manifest, video, root / "reports")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["sampled_scene_count"], 4)
            self.assertEqual(extract.call_count, 12)
            self.assertTrue(result["human_review_required"])
            self.assertFalse(result["publication_ready"])

    @patch("core.news_short_scene_qa._frame_difference", return_value=0.1)
    @patch("core.news_short_scene_qa._frame_metrics", return_value={"mean_luma": 90, "pixel_stddev": 30})
    @patch("core.news_short_scene_qa._extract_frame", return_value=True)
    @patch("core.news_short_scene_qa._probe_duration", return_value=20.0)
    def test_static_scene_is_review_not_automatic_failure(self, probe, extract, metrics, difference):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, video = self._manifest(root)
            result = audit_scene_footage(manifest, video, root / "reports")
            self.assertEqual(result["status"], "REVIEW")
            self.assertTrue(any("nearly identical" in warning for warning in result["warnings"]))

    @patch("core.news_short_scene_qa._frame_metrics", return_value={"mean_luma": 0, "pixel_stddev": 0})
    @patch("core.news_short_scene_qa._extract_frame", return_value=True)
    @patch("core.news_short_scene_qa._probe_duration", return_value=20.0)
    def test_black_scene_blocks(self, probe, extract, metrics):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, video = self._manifest(root)
            result = audit_scene_footage(manifest, video, root / "reports")
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("Near-black" in error for error in result["errors"]))

    @patch("core.news_short_scene_qa._probe_duration", return_value=10.0)
    def test_scene_overrun_blocks(self, probe):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, video = self._manifest(root, shots=[
                {"start_seconds": 0, "duration_seconds": 12}
            ])
            result = audit_scene_footage(manifest, video)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("beyond rendered video duration" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
