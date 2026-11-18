import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from core.quality import inspect_video


def test_quality_fingerprints_and_flags_freeze_and_failed_scan(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture")
    probe = {"format": {"duration": "10"}, "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
        {"codec_type": "audio"}]}

    def run(command, **kwargs):
        args = " ".join(map(str, command))
        if "freezedetect" in args:
            return SimpleNamespace(returncode=0, stdout="", stderr="freeze_start: 1\nfreeze_end: 5\n")
        if "blackdetect" in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="error")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("core.quality._run_json", return_value=probe), patch("core.quality.subprocess.run", side_effect=run):
        report = inspect_video(video, sample_count=0, profile="longform")
    assert report["video_sha256"] == hashlib.sha256(b"fixture").hexdigest()
    assert report["metrics"]["freeze_intervals"][0]["duration"] == 4
    assert any("Static or frozen" in warning for warning in report["warnings"])
    assert report["status"] == "FAIL"
    assert "Black-frame scan could not complete." in report["issues"]
