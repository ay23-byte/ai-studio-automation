import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.editor_longform import _seg

from PIL import Image

from core.visual_consistency import audit_visual_consistency, color_grade_filter


class VisualConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _frame(self, name, colour, size=(1280, 720), texture=False):
        path = self.root / name
        image = Image.new("RGB", (160, 90), colour)
        if texture:
            pixels = []
            for y in range(90):
                for x in range(160):
                    offset = 35 if (x // 8 + y // 8) % 2 else -35
                    pixels.append(tuple(max(0, min(255, channel + offset)) for channel in colour))
            image.putdata(pixels)
        image.resize(size).save(path)
        return str(path)

    def test_normal_sample_passes_basic_technical_checks(self):
        frame = self._frame("normal.jpg", (105, 110, 115), texture=True)
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_1", "sample_frames": [frame]}]}, self.root
        )
        self.assertEqual(report["shots"][0]["status"], "pass")
        self.assertEqual(report["summary"]["measured_shot_count"], 1)
        self.assertTrue((self.root / "visual_consistency_report.json").is_file())

    def test_severely_underexposed_shot_fails(self):
        frame = self._frame("dark.jpg", (0, 0, 0))
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_dark", "sample_frames": [frame]}]}, self.root
        )
        self.assertEqual(report["shots"][0]["status"], "fail")
        self.assertTrue(any("underexposed" in item for item in report["shots"][0]["issues"]))

    def test_missing_frames_are_reported_as_unavailable(self):
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_empty", "sample_frames": []}]}, self.root
        )
        self.assertEqual(report["shots"][0]["status"], "unavailable")
        self.assertEqual(report["summary"]["unavailable_count"], 1)

    def test_dark_scene_with_legitimate_black_regions_is_not_failed_for_clipping_alone(self):
        # Documentary night/space/archive scenes can contain large dark regions.
        # Clipping percentage alone must not fail a frame that retains tonal detail.
        path = self.root / "dark-scene.jpg"
        image = Image.new("RGB", (160, 90), (0, 0, 0))
        pixels = []
        for y in range(90):
            for x in range(160):
                if 35 <= x < 125 and 20 <= y < 70:
                    value = 50 + ((x + y) % 35)
                    pixels.append((value, value, value))
                else:
                    pixels.append((0, 0, 0))
        image.putdata(pixels)
        image.resize((1280, 720)).save(path)
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "night_scene", "sample_frames": [str(path)]}]}, self.root
        )
        self.assertNotEqual(report["shots"][0]["status"], "fail")
        self.assertEqual(report["summary"]["fail_count"], 0)

    def test_consistency_outlier_is_flagged_for_review(self):
        dark = self._frame("dark-mid.jpg", (80, 80, 80))
        bright = self._frame("bright-mid.jpg", (220, 220, 220))
        report = audit_visual_consistency(
            {"shots": [
                {"shot_id": "shot_a", "sample_frames": [dark]},
                {"shot_id": "shot_b", "sample_frames": [dark]},
                {"shot_id": "shot_c", "sample_frames": [bright]},
            ]}, self.root
        )
        self.assertEqual(report["shots"][2]["status"], "review")

    def test_colour_grade_presets_are_valid_filter_strings(self):
        presets = [color_grade_filter(name) for name in ("neutral", "cinematic", "warm", "cool", "archive")]
        self.assertEqual(len(set(presets)), 5)
        self.assertTrue(all("eq=" in item for item in presets))
        self.assertIn("colorbalance=", color_grade_filter("cinematic"))
        self.assertEqual(color_grade_filter("unknown"), color_grade_filter("cinematic"))


    def test_same_grade_is_applied_to_stills_and_video_segments(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"placeholder")
        with patch("core.editor_longform._run") as run:
            _seg(source, 0, 2.0, True, self.root)
            still_filter = run.call_args.args[0][run.call_args.args[0].index("-vf") + 1]
            _seg(source, 1, 2.0, False, self.root)
            video_filter = run.call_args.args[0][run.call_args.args[0].index("-vf") + 1]
        self.assertIn("colorbalance=", still_filter)
        self.assertIn("colorbalance=", video_filter)
        self.assertIn(color_grade_filter("cinematic"), still_filter)
        self.assertIn(color_grade_filter("cinematic"), video_filter)


if __name__ == "__main__":
    unittest.main()
