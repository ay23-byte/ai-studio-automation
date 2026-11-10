"""Tests for free cinematic FFmpeg motion profiles."""
import unittest
from core.cinematic_motion import movement_for, still_motion_parameters, video_crop_expressions

class CinematicMotionTests(unittest.TestCase):
    def test_movement_is_read_from_shot_plan(self):
        self.assertEqual(movement_for({"movement": "tracking"}, 0), "tracking")
        self.assertEqual(movement_for({"movement": "unknown"}, 1), "tracking")

    def test_still_push_and_pull_have_distinct_zoom_curves(self):
        push = still_motion_parameters({"movement": "slow_push_in"}, 0, 120)
        pull = still_motion_parameters({"movement": "slow_pull_out"}, 0, 120)
        self.assertIn("1.0+0.055", push[0])
        self.assertIn("1.055-0.055", pull[0])

    def test_video_pan_stays_inside_overscan(self):
        x, y = video_crop_expressions({"movement": "pan"}, 0, 5, 128, 72)
        self.assertIn("128*", x)
        self.assertEqual(y, "72/2")

    def test_locked_camera_does_not_drift(self):
        self.assertEqual(video_crop_expressions({"movement": "locked"}, 0, 5, 128, 72), ("128/2", "72/2"))

if __name__ == "__main__":
    unittest.main()