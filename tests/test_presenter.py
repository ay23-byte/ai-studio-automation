"""Tests for the optional presenter integration."""
import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
