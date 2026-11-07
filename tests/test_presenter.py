"""Tests for the optional presenter integration."""
import os
import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core.presenter import generate_section_presenter


class PresenterTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"PRESENTER_ENABLED": "0"}, clear=False):
            self.assertIsNone(generate_section_presenter(
                "missing.wav", 0, 5, 1, tempfile.mkdtemp()
            ))

    def test_enabled_requires_sadtalker_and_portrait(self):
        with patch.dict(os.environ, {
            "PRESENTER_ENABLED": "1",
            "SADTALKER_DIR": "/missing/sadtalker",
            "PRESENTER_IMAGE": "/missing/presenter.png",
        }, clear=False):
            with self.assertRaises(RuntimeError):
                generate_section_presenter(
                    "missing.wav", 0, 5, 1, tempfile.mkdtemp()
                )


    def test_official_sadtalker_checkpoints_and_cpu_flag_are_used(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "SadTalker"
            (repo / "checkpoints").mkdir(parents=True)
            (repo / "inference.py").write_text("# inference", encoding="utf-8")
            (repo / "checkpoints" / "SadTalker_V0.0.2_256.safetensors").write_bytes(b"model")
            (repo / "checkpoints" / "mapping_00229-model.pth.tar").write_bytes(b"mapping")
            portrait = root / "portrait.png"
            portrait.write_bytes(b"portrait")
            audio = root / "narration.wav"
            audio.write_bytes(b"audio")
            build = root / "build"

            def fake_run(command, **kwargs):
                if command[0] == "ffmpeg":
                    Path(command[-1]).write_bytes(b"audio segment")
                else:
                    result_dir = Path(command[command.index("--result_dir") + 1])
                    result_dir.mkdir(parents=True, exist_ok=True)
                    (result_dir / "generated.mp4").write_bytes(b"generated video")
                return None

            with patch.dict(os.environ, {
                "PRESENTER_ENABLED": "1",
                "SADTALKER_DIR": str(repo),
                "PRESENTER_IMAGE": str(portrait),
                "SADTALKER_DEVICE": "cpu",
            }, clear=False), patch("core.presenter.subprocess.run", side_effect=fake_run) as run, patch(
                "core.presenter_quality.inspect_presenter_clip",
                return_value={"status": "PASS", "lip_sync_verified": False, "video_duration_seconds": 5.0},
            ):
                output = generate_section_presenter(audio, 0, 5, 1, build)
            self.assertTrue(output.is_file())
            inference_command = run.call_args_list[-1].args[0]
            self.assertIn("--cpu", inference_command)
            metadata = json.loads(output.with_suffix(".mp4.source.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["presenter_mode"], "sadtalker")
            self.assertFalse(metadata["lip_sync_verified"])


if __name__ == "__main__":
    unittest.main()
