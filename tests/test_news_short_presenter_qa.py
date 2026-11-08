import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.news_short_presenter_qa import audit_presenter


class PresenterVisibilityQATests(unittest.TestCase):
    def _fixture(self, root, clips=True):
        (root / "review_short.mp4").write_bytes(b"video")
        shots = [{"start_seconds": i * 5, "duration_seconds": 5} for i in range(4)]
        entries = []
        for i in range(4):
            clip = root / f"clip_{i}.mp4"
            clip.write_bytes(b"x" * 12000)
            entries.append({"path": str(clip), "duration_seconds": 5})
        manifest = {
            "presenter_mode": "required_3d_talking_avatar_picture_in_picture",
            "shots": shots, "presenter_clips": entries if clips else entries[:3]
        }
        path = root / "production_manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def test_fails_closed_when_scene_presenter_clip_is_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._fixture(Path(temp), clips=False)
            result = audit_presenter(path, Path(temp) / "review_short.mp4")
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["presenter_visibility_verified"])

    def test_fails_when_presenter_mode_is_not_declared(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            data = json.loads(path.read_text())
            data["presenter_mode"] = "none"
            path.write_text(json.dumps(data))
            result = audit_presenter(path, root / "review_short.mp4")
            self.assertEqual(result["status"], "FAIL")

    @patch("core.news_short_presenter_qa._image_metrics", return_value={"mean_luma_proxy": 80, "pixel_stddev": 24})
    @patch("core.news_short_presenter_qa._sample_region", return_value=True)
    @patch("core.news_short_presenter_qa._probe", return_value={
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        "format": {"duration": "5.0"}
    })
    def test_all_scene_clips_and_visible_regions_require_human_review(self, probe, sample, metrics):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            result = audit_presenter(path, root / "review_short.mp4", root / "reports")
            self.assertEqual(result["status"], "REVIEW")
            self.assertEqual(result["presenter_clip_count"], 4)
            self.assertTrue(result["presenter_visibility_verified"])
            self.assertTrue(result["human_review_required"])
            self.assertFalse(result["publication_ready"])
            self.assertEqual(sample.call_count, 12)

    @patch("core.news_short_presenter_qa._image_metrics", return_value={"mean_luma_proxy": 0, "pixel_stddev": 0})
    @patch("core.news_short_presenter_qa._sample_region", return_value=True)
    @patch("core.news_short_presenter_qa._probe", return_value={
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        "format": {"duration": "5.0"}
    })
    def test_black_or_static_presenter_region_fails(self, probe, sample, metrics):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._fixture(root)
            result = audit_presenter(path, root / "review_short.mp4", root / "reports")
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["presenter_visibility_verified"])


if __name__ == "__main__":
    unittest.main()
