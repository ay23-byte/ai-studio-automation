import unittest

from core.avatar_clip_generation import presenter_intro_duration
from core.pipeline import validate_documentary_visual_coverage


class PresenterIntroDurationTests(unittest.TestCase):
    def test_long_chapter_gets_short_intro_not_full_chapter(self):
        self.assertEqual(presenter_intro_duration(90), 8.0)

    def test_short_chapter_intro_is_bounded_by_chapter(self):
        self.assertEqual(presenter_intro_duration(4), 4.0)

    def test_configured_intro_cap_is_respected(self):
        self.assertEqual(presenter_intro_duration(90, 6), 6.0)

    def test_invalid_chapter_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            presenter_intro_duration(0)


class DocumentaryVisualCoverageTests(unittest.TestCase):
    def test_accepts_documentary_with_broll_and_short_presenter_intro(self):
        timeline = [
            {"_dur": 8, "presenter_mode": "talking_avatar", "_media": {"path": "avatar.mp4"}},
            {"_dur": 82, "_media": {"path": "north-korea-street.mp4"}},
        ]
        report = validate_documentary_visual_coverage(timeline, 90)
        self.assertGreaterEqual(report["non_presenter_ratio"], 0.65)
        self.assertLessEqual(report["presenter_ratio"], 0.25)

    def test_rejects_full_chapter_avatar_only_render(self):
        timeline = [
            {"_dur": 90, "presenter_mode": "talking_avatar", "_media": {"path": "avatar.mp4"}},
        ]
        with self.assertRaisesRegex(RuntimeError, "Presenter footage occupies"):
            validate_documentary_visual_coverage(timeline, 90)

    def test_rejects_too_little_non_presenter_footage(self):
        timeline = [
            {"_dur": 30, "presenter_mode": "talking_avatar", "_media": {"path": "avatar.mp4"}},
            {"_dur": 60, "_media": {"path": "broll.mp4"}},
        ]
        with self.assertRaisesRegex(RuntimeError, "non-presenter visuals"):
            validate_documentary_visual_coverage(timeline, 90)


if __name__ == "__main__":
    unittest.main()
