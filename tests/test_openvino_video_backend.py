"""Offline tests for the optional OpenVINO LTX backend."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.openvino_video import generate_ltx_video
from core.images import generate_ai_video


class OpenVINOVideoBackendTests(unittest.TestCase):
    def test_missing_model_configuration_fails_closed_without_importing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            self.assertFalse(generate_ltx_video("A cinematic scene", Path(tmp) / "clip.mp4"))
            self.assertFalse((Path(tmp) / "clip.mp4").exists())

    def test_missing_model_configuration_never_calls_hosted_inference(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            self.assertFalse(generate_ai_video("A cinematic scene", Path(tmp) / "clip.mp4"))
            self.assertFalse((Path(tmp) / "clip.mp4").exists())


    def test_local_pipeline_output_is_encoded_and_marked_as_synthetic(self):
        import sys
        import types
        import numpy as np

        class FakePipeline:
            def __init__(self, model_dir, device):
                self.model_dir = model_dir
                self.device = device

            def generate(self, prompt, **kwargs):
                self.kwargs = kwargs
                frames = np.zeros((1, 9, kwargs["height"], kwargs["width"], 3), dtype=np.uint8)
                return types.SimpleNamespace(video=types.SimpleNamespace(data=frames))

        fake_genai = types.SimpleNamespace(Text2VideoPipeline=FakePipeline)
        fake_openvino = types.SimpleNamespace(Core=lambda: types.SimpleNamespace(available_devices=["CPU", "GPU.0"]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model"
            model.mkdir()
            output = root / "generated.mp4"

            def fake_ffmpeg(command, input=None, check=None, capture_output=None, timeout=None):
                output.write_bytes(b"mock mp4 bytes" * 5000)
                return types.SimpleNamespace(returncode=0)

            with patch.dict(os.environ, {
                "OPENVINO_LTX_MODEL_DIR": str(model),
                "OPENVINO_DEVICE": "GPU",
                "OPENVINO_VIDEO_WIDTH": "320",
                "OPENVINO_VIDEO_HEIGHT": "320",
                "OPENVINO_VIDEO_FRAMES": "9",
                "OPENVINO_VIDEO_STEPS": "2",
                "OPENVINO_VIDEO_FPS": "12",
            }, clear=True), patch.dict(sys.modules, {
                "openvino": fake_openvino,
                "openvino_genai": fake_genai,
            }), patch("core.openvino_video.subprocess.run", side_effect=fake_ffmpeg):
                self.assertTrue(generate_ltx_video("A cinematic scene", output))

            self.assertTrue(output.is_file())
            metadata = __import__("json").loads(
                output.with_suffix(".mp4.source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["asset_type"], "ai_generated_video")
            self.assertTrue(metadata["ai_disclosure"])
            self.assertTrue(metadata["human_visual_inspection_required"])

    def test_archival_prompt_is_not_sent_to_synthetic_video_generator(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "OPENVINO_LTX_MODEL_DIR": str(Path(tmp) / "nonexistent-model")
        }, clear=True):
            self.assertFalse(generate_ai_video("authentic archival newsreel footage", Path(tmp) / "clip.mp4"))


if __name__ == "__main__":
    unittest.main()
