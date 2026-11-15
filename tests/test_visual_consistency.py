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

    def test_a_night_sky_is_a_dark_scene_to_review_not_a_black_frame(self):
        # Production build 43655186698 (a space documentary) failed technical QA after an
        # 80-minute build. A deep-field picture is almost black on average and is not a defect.
        path = self.root / "stars.png"
        image = Image.new("RGB", (1280, 720), (2, 2, 4))
        for index in range(900):
            x, y = (index * 197) % 1270, (index * 113) % 710
            for dx in range(6):
                for dy in range(6):
                    image.putpixel((x + dx, y + dy), (235, 235, 225))
        image.save(path)
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_sky", "sample_frames": [str(path)]}]}, self.root
        )
        shot = report["shots"][0]
        self.assertLess(shot["metrics"]["brightness"], 12)
        self.assertGreaterEqual(shot["metrics"]["highlight_level"], 60)
        self.assertEqual(shot["status"], "review")
        self.assertTrue(any("Dark scene" in item for item in shot["issues"]))
        self.assertEqual(report["summary"]["fail_count"], 0)

    def test_a_dim_frame_without_highlights_still_fails(self):
        frame = self._frame("dim.jpg", (9, 9, 9))
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_dim", "sample_frames": [frame]}]}, self.root
        )
        self.assertEqual(report["shots"][0]["status"], "fail")

    def test_a_small_source_fails_and_the_message_gives_its_size(self):
        frame = self._frame("small.jpg", (105, 110, 115), size=(480, 360), texture=True)
        report = audit_visual_consistency(
            {"shots": [{"shot_id": "shot_small", "sample_frames": [frame]}]}, self.root
        )
        self.assertEqual(report["shots"][0]["status"], "fail")
        self.assertIn("480×360", " ".join(report["shots"][0]["issues"]))

    def test_the_footage_floor_for_a_documentary_matches_this_audit(self):
        import re
        from core import visual_consistency
        source = (Path(__file__).resolve().parents[1] / "core" / "pipeline.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'setdefault\("FOOTAGE_MIN_WIDTH", str\(visual_consistency\.MIN_SOURCE_WIDTH\)\)')
        self.assertRegex(source, r'setdefault\("FOOTAGE_MIN_HEIGHT", str\(visual_consistency\.MIN_SOURCE_HEIGHT\)\)')
        self.assertEqual((visual_consistency.MIN_SOURCE_WIDTH, visual_consistency.MIN_SOURCE_HEIGHT), (640, 360))
        self.assertTrue(re.search(r"FOOTAGE_MIN_WIDTH", source))

    def test_a_clip_is_judged_on_the_seconds_its_shot_shows(self):
        from core.visual_consistency import sample_times
        self.assertEqual(sample_times(600.0, 8.0), [2.0, 4.0, 6.0])       # a ten-minute source, an 8 s shot
        self.assertEqual(sample_times(4.0, 12.0), [1.0, 2.0, 3.0])        # the clip is shorter than the shot
        self.assertEqual(sample_times(8.0), [2.0, 4.0, 6.0])              # no shot length known

    def test_the_review_sampler_passes_the_shot_length(self):
        source = (Path(__file__).resolve().parents[1] / "core" / "visual_production.py").read_text(encoding="utf-8")
        self.assertIn('_sample_frames(path, frame_dir, shot_id, window_seconds=shot.get("_dur"))', source)

    def test_a_downloaded_file_is_measured_with_the_audit_rule(self):
        from core.visual_consistency import media_picture_problem
        black = self.root / "black.png"
        Image.new("RGB", (1280, 720), (3, 3, 3)).save(black)
        self.assertIn("underexposed", media_picture_problem(black))
        normal = Path(self._frame("fine.jpg", (105, 110, 115), texture=True))
        self.assertIsNone(media_picture_problem(normal))
        # Size is the validator's business (a vertical Short is narrower than a documentary).
        narrow = Path(self._frame("narrow.jpg", (105, 110, 115), size=(540, 960), texture=True))
        self.assertIsNone(media_picture_problem(narrow))
        broken = self.root / "broken.mp4"
        broken.write_bytes(b"not a video")
        self.assertIsNone(media_picture_problem(broken))
        self.assertIsNone(media_picture_problem(self.root / "absent.mp4"))

    def test_a_borderline_dark_picture_is_refused_at_download_but_only_once(self):
        # Mean level 13 passes the audit (limit 12) and is refused when choosing footage (limit 15),
        # so a file accepted there cannot be the one that fails the finished film.
        from core.visual_consistency import media_picture_problem
        path = self.root / "dim.png"
        Image.new("RGB", (1280, 720), (13, 13, 13)).save(path)
        self.assertIn("underexposed", media_picture_problem(path))
        report = audit_visual_consistency({"shots": [{"shot_id": "s", "sample_frames": [str(path)]}]}, self.root)
        self.assertNotEqual(report["shots"][0]["status"], "fail")

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
