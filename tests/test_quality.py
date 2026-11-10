import unittest
from unittest.mock import patch
from pathlib import Path
from core.quality import _seconds, _hamming
from core.pipeline import _quality_check


class QualityInspectorUnitTests(unittest.TestCase):
    def test_seconds_handles_bad_values(self):
        self.assertEqual(_seconds("12.5"), 12.5)
        self.assertEqual(_seconds(None), 0.0)
        self.assertEqual(_seconds("not-a-number"), 0.0)

    def test_hamming_distance_counts_changed_bits(self):
        self.assertEqual(_hamming(0b1010, 0b1001), 2)
        self.assertEqual(_hamming(255, 255), 0)

    def test_render_quality_failure_blocks_completion(self):
        report = {"status": "FAIL", "score": 60, "issues": ["No audio stream found."], "warnings": []}
        with patch("core.quality.inspect_video", return_value=report), patch("core.quality.write_report", return_value=Path("quality_report.json")), patch("core.pipeline.update_video"):
            with self.assertRaisesRegex(RuntimeError, "quality inspection failed"):
                _quality_check(1, Path("video.mp4"), Path("captions.ass"), Path("build"))

    def test_quality_inspection_exception_blocks_completion(self):
        with patch("core.quality.inspect_video", side_effect=OSError("ffprobe missing")):
            with self.assertRaisesRegex(RuntimeError, "could not complete"):
                _quality_check(1, Path("video.mp4"), Path("captions.ass"), Path("build"))

if __name__ == "__main__":
    unittest.main()
