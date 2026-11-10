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

    def test_archival_prompt_is_not_sent_to_synthetic_video_generator(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "OPENVINO_LTX_MODEL_DIR": str(Path(tmp) / "nonexistent-model")
        }, clear=True):
            self.assertFalse(generate_ai_video("authentic archival newsreel footage", Path(tmp) / "clip.mp4"))


if __name__ == "__main__":
    unittest.main()
