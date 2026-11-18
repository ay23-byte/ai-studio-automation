import unittest
from unittest.mock import patch
from pathlib import Path
from core.quality import _seconds, _hamming, _black_frame_issue, _near_duplicate_pairs
from core.pipeline import _quality_check


class QualityInspectorUnitTests(unittest.TestCase):
    def test_seconds_handles_bad_values(self):
        self.assertEqual(_seconds("12.5"), 12.5)
        self.assertEqual(_seconds(None), 0.0)
        self.assertEqual(_seconds("not-a-number"), 0.0)

    def test_hamming_distance_counts_changed_bits(self):
        self.assertEqual(_hamming(0b1010, 0b1001), 2)
        self.assertEqual(_hamming(255, 255), 0)

    def test_duplicate_samples_inside_one_shot_are_not_counted_as_repeated_shots(self):
        hashes = [(0, 0), (1, 0), (2, 0), (3, 0xFFFFFFFF), (4, 0xFFFFFFFF), (5, 0xFFFFFFFF),
                  (6, 0xAAAAAAAA), (7, 0xAAAAAAAA), (8, 0xAAAAAAAA),
                  (9, 0x55555555), (10, 0x55555555), (11, 0x55555555)]
        self.assertEqual(_near_duplicate_pairs(hashes, 12), [])

    def test_same_visual_reappearing_in_distant_shots_is_counted(self):
        hashes = [(0, 0), (1, 0xFFFFFFFF), (2, 0xAAAAAAAA), (3, 0x55555555),
                  (4, 0xF0F0F0F0), (5, 0x0F0F0F0F), (6, 0xCCCCCCCC),
                  (7, 0x33333333), (8, 0xFF00FF00), (9, 0x00FF00FF),
                  (10, 0x96969696), (11, 0)]
        self.assertEqual(_near_duplicate_pairs(hashes, 12), [[0, 11]])

    def test_black_footage_is_measured_against_programme_not_long_credits_slate(self):
        issue, ratio = _black_frame_issue(65.23, 1376.8, body_seconds=1214.24)
        self.assertIsNotNone(issue)
        self.assertGreater(ratio, 0.03)
        self.assertIn("programme", issue)

    def test_short_black_transition_does_not_fail(self):
        issue, ratio = _black_frame_issue(1.0, 1376.8, body_seconds=1214.24)
        self.assertIsNone(issue)
        self.assertLess(ratio, 0.03)

    def test_black_ratio_uses_full_duration_when_body_duration_is_unknown(self):
        issue, ratio = _black_frame_issue(2.0, 100.0)
        self.assertIsNone(issue)
        self.assertAlmostEqual(ratio, 0.02)

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
