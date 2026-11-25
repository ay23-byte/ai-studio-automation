import os
import unittest
from unittest.mock import patch

from core.footage_sources import clip_size_limit_mb


class FootageClipSizeLimitTests(unittest.TestCase):
    def test_default_allows_bounded_large_archival_clips(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FOOTAGE_MAX_CLIP_MB", None)
            self.assertEqual(clip_size_limit_mb(), 450.0)

    def test_environment_can_lower_or_raise_the_per_clip_limit(self):
        with patch.dict(os.environ, {"FOOTAGE_MAX_CLIP_MB": "320"}):
            self.assertEqual(clip_size_limit_mb(), 320.0)
        with patch.dict(os.environ, {"FOOTAGE_MAX_CLIP_MB": "500"}):
            self.assertEqual(clip_size_limit_mb(), 500.0)

    def test_invalid_setting_falls_back_to_safe_default(self):
        with patch.dict(os.environ, {"FOOTAGE_MAX_CLIP_MB": "not-a-number"}):
            self.assertEqual(clip_size_limit_mb(), 450.0)

    def test_non_positive_setting_is_clamped(self):
        with patch.dict(os.environ, {"FOOTAGE_MAX_CLIP_MB": "0"}):
            self.assertEqual(clip_size_limit_mb(), 1.0)


if __name__ == "__main__":
    unittest.main()
