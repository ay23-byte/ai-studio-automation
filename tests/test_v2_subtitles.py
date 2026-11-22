"""Milestone 4: speech-aligned English subtitles from final narration."""
from __future__ import annotations

import unittest

from src.production_v2.config import V2Config
from src.production_v2.models import NarrationSegment
from src.production_v2.subtitles import (build_ass, build_subtitles, parse_srt,
                                         timestamp, validate_subtitles)


def segments() -> list[NarrationSegment]:
    return [
        NarrationSegment("s1", "This is the first spoken sentence about energy.", 0.0, 3.0, "a.wav", "mock"),
        NarrationSegment("s2", "The second sentence explains momentum and force.", 3.0, 6.0, "b.wav", "mock"),
    ]


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.config = V2Config.from_env({})

    def test_timestamp_format(self):
        self.assertEqual(timestamp(61.25), "00:01:01,250")

    def test_subtitles_are_aligned_and_readable(self):
        srt = build_subtitles(segments(), self.config)
        assessment = validate_subtitles(srt, segments(), self.config, video_duration=6.0)
        self.assertTrue(assessment.accepted, assessment.issues)
        cues = parse_srt(srt)
        self.assertGreaterEqual(len(cues), 2)
        self.assertLessEqual(cues[0][1], cues[1][1])

    def test_subtitles_come_from_final_narration_text(self):
        srt = build_subtitles(segments(), self.config)
        self.assertIn("first spoken sentence", srt)
        self.assertNotIn("Hook", srt)

    def test_devanagari_is_rejected(self):
        bad = [NarrationSegment("s1", "यह परीक्षण है", 0, 2, "a.wav", "mock")]
        srt = build_subtitles(bad, self.config)
        self.assertFalse(validate_subtitles(srt, bad, self.config, 2.0).accepted)

    def test_overlapping_cues_are_rejected(self):
        bad = [NarrationSegment("s1", "word " * 20, 0, 2, "a.wav", "mock"),
               NarrationSegment("s2", "word " * 20, 1, 4, "b.wav", "mock")]
        srt = build_subtitles(bad, self.config)
        self.assertTrue(validate_subtitles(srt, bad, self.config, 5.0).accepted)
        injected = ("1\n00:00:00,000 --> 00:00:03,000\nfirst\n\n"
                    "2\n00:00:02,000 --> 00:00:04,000\nsecond\n")
        self.assertFalse(validate_subtitles(injected, bad, self.config, 5.0).accepted)

    def test_ass_track_is_generated(self):
        ass = build_ass(segments(), self.config)
        self.assertIn("[V4+ Styles]", ass)
        self.assertIn("Dialogue:", ass)

    def test_documentary_captions_group_readable_lines_instead_of_tiny_fragments(self):
        long = [NarrationSegment(
            "s1",
            "Earth's magnetic field is generated deep inside the planet by the movement of molten iron in the outer core.",
            0.0, 8.0, "a.wav", "mock"
        )]
        srt = build_subtitles(long, self.config)
        cues = parse_srt(srt)
        self.assertLessEqual(len(cues), 2)
        self.assertTrue(all(len(text.split()) >= 4 for _, _, text in cues))
        self.assertTrue(all(len(text) <= 130 for _, _, text in cues))

    def test_ass_captions_use_two_line_breaks_and_landscape_documentary_canvas(self):
        ass = build_ass(segments(), self.config)
        self.assertIn("PlayResX: 1920", ass)
        self.assertIn("PlayResY: 1080", ass)
        self.assertIn(r"\\N", ass)


if __name__ == "__main__":
    unittest.main()
