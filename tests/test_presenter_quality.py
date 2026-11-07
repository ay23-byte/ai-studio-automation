import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.presenter_quality import inspect_presenter_clip, write_presenter_metadata


class PresenterQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clip = self.root / "presenter.mp4"
        self.clip.write_bytes(b"placeholder")

    def _probe(self, audio_duration="5.0", fps="25/1", width=512, height=512):
        return json.dumps({
            "format": {"duration": "5.0"},
            "streams": [
                {"codec_type": "video", "duration": "5.0", "width": width,
                 "height": height, "avg_frame_rate": fps},
                {"codec_type": "audio", "duration": audio_duration},
            ],
        })

    @patch("core.presenter_quality.subprocess.check_output")
    def test_valid_audio_driven_presenter_passes_technical_checks(self, probe):
        probe.return_value = self._probe()
        report = inspect_presenter_clip(self.clip, expected_duration=5.0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["av_duration_delta_seconds"], 0.0)
        self.assertFalse(report["lip_sync_verified"])

    @patch("core.presenter_quality.subprocess.check_output")
    def test_audio_video_duration_mismatch_fails(self, probe):
        probe.return_value = self._probe(audio_duration="3.0")
        report = inspect_presenter_clip(self.clip, expected_duration=5.0)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("durations differ" in item for item in report["issues"]))

    @patch("core.presenter_quality.subprocess.check_output")
    def test_missing_audio_fails_when_audio_is_required(self, probe):
        probe.return_value = json.dumps({
            "format": {"duration": "5.0"},
            "streams": [{"codec_type": "video", "duration": "5.0",
                         "width": 512, "height": 512, "avg_frame_rate": "25/1"}],
        })
        report = inspect_presenter_clip(self.clip, require_audio=True)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("no embedded audio" in item for item in report["issues"]))

    @patch("core.presenter_quality.subprocess.check_output")
    def test_low_frame_rate_is_review_not_false_lipsync_claim(self, probe):
        probe.return_value = self._probe(fps="12/1")
        report = inspect_presenter_clip(self.clip)
        self.assertEqual(report["status"], "REVIEW")
        self.assertFalse(report["lip_sync_verified"])

    def test_presenter_sidecar_preserves_existing_source_metadata(self):
        sidecar = self.clip.with_suffix(".mp4.source.json")
        sidecar.write_text(json.dumps({"provider": "User supplied"}), encoding="utf-8")
        write_presenter_metadata(self.clip, {
            "presenter_mode": "talking_avatar", "lip_sync_verified": False,
        })
        saved = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "User supplied")
        self.assertEqual(saved["presenter_mode"], "talking_avatar")


if __name__ == "__main__":
    unittest.main()
