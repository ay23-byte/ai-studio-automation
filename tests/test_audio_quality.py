import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.audio_quality import (
    _clipped_samples, _loudnorm_data, _silence_intervals,
    assert_audio_quality_ready, inspect_audio, mark_audio_reviewed,
)
from core.editor_longform import _audio_master_filter


class AudioQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "sample.mp4"
        self.video.write_bytes(b"placeholder")

    def _loud_log(self, integrated="-16.2", peak="-2.0", lra="7.0"):
        return json.dumps({
            "input_i": integrated, "input_tp": peak, "input_lra": lra,
            "input_thresh": "-26.0", "output_i": "-16.0", "output_tp": "-1.8",
            "output_lra": lra, "output_thresh": "-26.0", "target_offset": "0.2",
        })

    def _completed(self, stderr=""):
        return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout="", stderr=stderr)

    def test_parsers_read_loudness_clipping_and_silence(self):
        self.assertEqual(_loudnorm_data(self._loud_log())["input_i"], -16.2)
        self.assertEqual(_clipped_samples("Number of samples out of range: 4"), 4)
        # Real astats output has no such counter; the Overall peak decides.
        clean = "[Parsed_astats_0] Channel: 1\nPeak level dB: 0.000000\n[Parsed_astats_0] Overall\nPeak level dB: -4.091496\nPeak count: 2.000000\n"
        clipped = "[Parsed_astats_0] Overall\nPeak level dB: 0.000000\nPeak count: 37.000000\n"
        self.assertEqual(_clipped_samples(clean), 0)
        self.assertEqual(_clipped_samples(clipped), 37)
        self.assertIsNone(_clipped_samples("no statistics here"))
        intervals = _silence_intervals("silence_start: 1.0\nsilence_end: 5.5 | silence_duration: 4.5")
        self.assertEqual(intervals[0]["duration"], 4.5)

    @patch("core.audio_quality.subprocess.run")
    def test_audio_measurements_pass_with_safe_levels(self, run):
        run.side_effect = [
            self._completed(self._loud_log()),
            self._completed("Number of samples out of range: 0"),
            self._completed(""),
        ]
        report = inspect_audio(self.video)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["metrics"]["integrated_lufs"], -16.2)
        self.assertEqual(report["human_review_status"], "pending")

    @patch("core.audio_quality.subprocess.run")
    def test_true_peak_and_clipping_fail_audio(self, run):
        run.side_effect = [
            self._completed(self._loud_log(integrated="-14.0", peak="-0.2")),
            self._completed("Number of samples out of range: 3"),
            self._completed(""),
        ]
        report = inspect_audio(self.video)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["issues"])

    def test_human_listening_review_is_required_and_persisted(self):
        path = self.root / "audio_quality_report.json"
        path.write_text(json.dumps({
            "status": "REVIEW", "human_review_status": "pending",
            "metrics": {"integrated_lufs": -18.5}, "warnings": ["Loudness below target."],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "listening review is pending"):
            assert_audio_quality_ready(self.root)
        saved = mark_audio_reviewed(self.root, "Narration clear; music balanced.")
        self.assertEqual(saved["human_review_status"], "reviewed")
        self.assertTrue(saved["reviewed_at"])
        self.assertEqual(assert_audio_quality_ready(self.root)["reviewer_notes"],
                         "Narration clear; music balanced.")

    def test_failed_audio_cannot_be_approved_even_if_reviewed(self):
        path = self.root / "audio_quality_report.json"
        path.write_text(json.dumps({
            "status": "FAIL", "human_review_status": "reviewed",
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "blocking failures"):
            assert_audio_quality_ready(self.root)

    def test_master_filter_uses_bounded_documentary_targets(self):
        with patch.dict("os.environ", {
            "DOCUMENTARY_TARGET_LUFS": "-15",
            "DOCUMENTARY_TRUE_PEAK_DBFS": "-2",
            "DOCUMENTARY_TARGET_LRA": "9",
        }):
            value = _audio_master_filter()
        self.assertEqual(value, "loudnorm=I=-15.0:TP=-2.0:LRA=9.0")


if __name__ == "__main__":
    unittest.main()
