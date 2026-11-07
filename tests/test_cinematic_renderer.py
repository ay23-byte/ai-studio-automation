import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cinematic_renderer import _scene_frame, render_cinematic_video


class CinematicRendererTests(unittest.TestCase):
    def test_frame_is_deterministic_for_same_scene_and_time(self):
        scene = {"caption": "Test headline", "source_ids": ["S1"]}
        a = _scene_frame(scene, 0, 1.25, 4.0, 1.25)
        b = _scene_frame(scene, 0, 1.25, 4.0, 1.25)
        self.assertEqual(a.size, (1080, 1920))
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_rejects_empty_or_mismatched_timeline(self):
        with self.assertRaisesRegex(ValueError, "equal length"):
            render_cinematic_video([], [2.0], "unused.mp4")

    def test_rejects_invalid_durations(self):
        with self.assertRaisesRegex(ValueError, "finite positive"):
            render_cinematic_video([{}], [0], "unused.mp4")

    def test_rejects_invalid_fps(self):
        with self.assertRaisesRegex(ValueError, "fps"):
            render_cinematic_video([{}], [1], "unused.mp4", fps=0)


if __name__ == "__main__":
    unittest.main()
