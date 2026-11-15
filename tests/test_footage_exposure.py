import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.footage_validation import _exposure_summary, validate_media


class FootageExposureTests(unittest.TestCase):
    def test_consistently_near_black_frames_are_rejected(self):
        result = _exposure_summary([2.1, 3.8, 1.5])
        self.assertEqual(result["status"], "underexposed")
        self.assertLess(result["mean_brightness"], 12.0)

    def test_readable_frames_pass_exposure_floor(self):
        result = _exposure_summary([18.0, 42.0, 55.0])
        self.assertEqual(result["status"], "usable")

    def test_missing_samples_are_not_misclassified_as_dark(self):
        result = _exposure_summary([])
        self.assertEqual(result["status"], "unavailable")

    def test_validate_media_rejects_dark_candidate_before_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.mp4"
            path.write_bytes(b"x" * 60000)
            facts = {
                "container": "mov,mp4,m4a,3gp,3g2,mj2", "duration_seconds": 30.0,
                "width": 1920, "height": 1080, "has_video": True,
            }
            with patch("core.footage_validation.inspect_media", return_value=facts), \
                 patch("core.footage_validation.decode_check", return_value=""), \
                 patch("core.footage_validation.inspect_visual_exposure", return_value={
                     "status": "underexposed", "mean_brightness": 3.8,
                     "sample_brightness": [3.9, 3.3, 4.2], "threshold": 12.0,
                 }):
                result = validate_media(path, with_hash=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "underexposed")
            self.assertIn("too dark", result["reason"])


if __name__ == "__main__":
    unittest.main()
