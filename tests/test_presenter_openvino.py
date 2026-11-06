"""Unit tests for the optional OpenVINO presenter adapter."""
import os
import tempfile
import unittest
from unittest.mock import patch

from core.presenter_openvino import available_devices, generate_section_presenter


class OpenVINODeviceTests(unittest.TestCase):
    def test_devices_empty_when_runtime_missing(self):
        with patch.dict("sys.modules", {"openvino": None}):
            self.assertEqual(available_devices(), [])

    def test_disabled_returns_none_without_runtime(self):
        with patch.dict(os.environ, {"PRESENTER_ENABLED": "0"}, clear=False):
            self.assertIsNone(generate_section_presenter(
                "missing.wav", 0, 4, 1, tempfile.mkdtemp()
            ))

    def test_missing_gpu_is_actionable(self):
        with patch("core.presenter_openvino.available_devices", return_value=["CPU"]), \
             patch.dict(os.environ, {"PRESENTER_ENABLED": "1", "OPENVINO_DEVICE": "GPU"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                generate_section_presenter(
                    "missing.wav", 0, 4, 1, tempfile.mkdtemp()
                )


if __name__ == "__main__":
    unittest.main()
