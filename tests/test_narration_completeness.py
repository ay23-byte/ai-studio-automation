"""Narration audio must contain every word it was given, and reach the requested runtime."""
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core import voice
from core.pipeline import measured_runtime_report
from tests.footage_fixture import have_ffmpeg

RATE = 44100


def _tone(seconds, frequency=220.0, level=0.4):
    t = np.arange(int(seconds * RATE)) / RATE
    return (np.sin(2 * np.pi * frequency * t) * level * 32767).astype("<i2")


def _silence(seconds):
    return np.zeros(int(seconds * RATE), dtype="<i2")


def _write_wav(path, parts):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.concatenate(parts).tobytes())


def _seconds(path):
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


class TrimEdgeSilenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.wav = Path(self.temp.name) / "phrase.wav"

    def test_pauses_inside_the_phrase_are_kept(self):
        # speech, a 0.35 s comma pause, speech, a 0.6 s sentence pause, speech
        _write_wav(self.wav, [_silence(0.3), _tone(1.5), _silence(0.35), _tone(2.0), _silence(0.6),
                              _tone(1.0, 330.0), _silence(0.9)])
        result = voice.trim_edge_silence(self.wav)
        spoken = 1.5 + 0.35 + 2.0 + 0.6 + 1.0
        self.assertAlmostEqual(_seconds(self.wav), spoken + 0.06, delta=0.02)   # 0.03 s kept at each end
        self.assertAlmostEqual(result["removed_start_seconds"], 0.27, delta=0.01)
        self.assertAlmostEqual(result["removed_end_seconds"], 0.87, delta=0.01)
        self.assertAlmostEqual(result["source_seconds"], 6.65, delta=0.01)

    def test_a_phrase_without_edge_silence_is_left_alone(self):
        _write_wav(self.wav, [_tone(1.0)])
        before = self.wav.read_bytes()
        voice.trim_edge_silence(self.wav)
        self.assertEqual(self.wav.read_bytes(), before)

    def test_a_silent_file_is_not_emptied(self):
        _write_wav(self.wav, [_silence(1.0)])
        result = voice.trim_edge_silence(self.wav)
        self.assertAlmostEqual(_seconds(self.wav), 1.0, delta=0.001)
        self.assertEqual(result["removed_end_seconds"], 0.0)

    def test_quiet_noise_below_the_threshold_counts_as_silence(self):
        noise = (np.random.default_rng(1).normal(0, 20, int(0.5 * RATE))).astype("<i2")   # about -64 dBFS
        _write_wav(self.wav, [noise, _tone(1.0), noise])
        voice.trim_edge_silence(self.wav)
        self.assertAlmostEqual(_seconds(self.wav), 1.06, delta=0.02)

    @unittest.skipUnless(have_ffmpeg(), "FFmpeg is required")
    def test_decoding_a_phrase_keeps_everything_after_its_first_pause(self):
        import subprocess
        from core.ffmpeg_utils import ffmpeg_bin
        source = Path(self.temp.name) / "source.wav"
        _write_wav(source, [_silence(0.3), _tone(1.5), _silence(0.35), _tone(2.0), _silence(0.9)])
        mp3 = Path(self.temp.name) / "phrase.mp3"
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(source), str(mp3)], check=True, timeout=60)
        voice._to_wav(mp3, self.wav)
        # The old filter stopped at the first pause and returned about 1.5 s of this 3.85 s phrase.
        self.assertGreater(_seconds(self.wav), 3.7)
        self.assertLess(_seconds(self.wav), 4.1)


class NarrationCompletenessTests(unittest.TestCase):
    PHRASE = "The telescope unfolded its mirror over two weeks, a quarter of a million miles from any repair crew."

    def test_natural_speech_passes_and_is_measured(self):
        sentences = [self.PHRASE] * 10
        report = voice.check_narration_complete(sentences, [6.0] * 10)        # 18 words in 6 s = 180 a minute
        self.assertEqual(report["words"], 180)
        self.assertAlmostEqual(report["words_per_minute"], 180.0)
        self.assertLess(report["characters_per_second"], 20)

    def test_audio_cut_at_the_first_pause_is_refused(self):
        sentences = [self.PHRASE] * 10
        with self.assertRaises(voice.NarrationIncompleteError) as caught:
            voice.check_narration_complete(sentences, [1.8] * 10)
        self.assertIn("Part of the narration was not spoken", str(caught.exception))
        self.assertIn("180 words", str(caught.exception))

    def test_one_truncated_phrase_is_named(self):
        sentences = [self.PHRASE] * 10
        durations = [6.0] * 10
        durations[6] = 1.2
        with self.assertRaises(voice.NarrationIncompleteError) as caught:
            voice.check_narration_complete(sentences, durations)
        self.assertIn("phrase 7 of 10", str(caught.exception))

    def test_short_phrases_are_not_judged_alone(self):
        voice.check_narration_complete(["Yes.", self.PHRASE], [0.1, 6.0])

    def test_the_limit_is_configurable(self):
        with patch.dict(os.environ, {"NARRATION_MAX_CHARS_PER_SECOND": "12"}):
            with self.assertRaises(voice.NarrationIncompleteError):
                voice.check_narration_complete([self.PHRASE] * 4, [6.0] * 4)

    def test_synthesise_refuses_a_narration_that_lost_its_words(self):
        with tempfile.TemporaryDirectory() as folder:
            def fake_edge(sentences, language, gender, build_dir):
                paths = []
                for index, _sentence in enumerate(sentences):
                    path = Path(build_dir) / f"s{index}.wav"
                    _write_wav(path, [_tone(1.0)])                       # one second for a 100 character phrase
                    paths.append(path)
                return paths
            with patch.object(voice, "_synth_all_edge", fake_edge), patch.object(voice, "_dur", _seconds), \
                    patch.object(voice, "_join") as join, patch.dict(os.environ, {"TTS_ENGINE": "edge"}):
                with self.assertRaises(voice.NarrationIncompleteError):
                    voice._synthesise_raw(" ".join([self.PHRASE] * 6), folder, language="english")
                join.assert_not_called()                                    # refused before the track is assembled


class MeasuredRuntimeTests(unittest.TestCase):
    def _script(self, minutes=18):
        return {"runtime_validation": {"target_minutes": minutes}}

    def test_a_six_minute_narration_for_an_eighteen_minute_request_fails(self):
        report = measured_runtime_report(self._script(), "word " * 2500, 377.0)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("6.3 minutes, 35% of the requested 18 minutes", report["issue"])
        self.assertIn("2500 words", report["issue"])

    def test_a_narration_near_the_request_passes(self):
        report = measured_runtime_report(self._script(), "word " * 2600, 16.5 * 60)
        self.assertEqual(report["status"], "PASS")
        self.assertAlmostEqual(report["fraction_of_target"], 0.9167, places=3)
        self.assertAlmostEqual(report["words_per_minute"], 157.6, places=1)
        self.assertIsNone(report["issue"])

    def test_the_floor_is_configurable(self):
        with patch.dict(os.environ, {"LONGFORM_MIN_MEASURED_RUNTIME_FRACTION": "0.95"}):
            self.assertEqual(measured_runtime_report(self._script(), "word " * 2600, 16.5 * 60)["status"], "FAIL")

    def test_a_script_without_a_requested_runtime_is_measured_but_not_refused(self):
        report = measured_runtime_report({}, "word " * 300, 120.0)
        self.assertEqual(report["status"], "NOT_APPLICABLE")
        self.assertEqual(report["words"], 300)

    def test_the_build_checks_runtime_before_fetching_visuals(self):
        text = (Path(__file__).resolve().parent.parent / "core/pipeline.py").read_text(encoding="utf-8")
        body = text[text.index("def build_longform"):]
        self.assertLess(body.index("measured_runtime_report(script"), body.index("footage_report.begin_run"))


if __name__ == "__main__":
    unittest.main()


class SplitLongBeatTests(unittest.TestCase):
    def _shot(self, seconds, start=100.0):
        return {"_start": start, "_dur": seconds, "narration": "anchor", "visual_type": "stock",
                "_media": {"type": "video", "path": "first.mp4"}}

    def test_a_beat_within_the_limit_is_untouched(self):
        from core.pipeline import split_long_beat
        shot = self._shot(11.0)
        self.assertEqual(split_long_beat(shot, lambda number: self.fail("not needed"), 12.0), [shot])
        self.assertNotIn("beat_part", shot)

    def test_a_long_beat_is_covered_by_different_pictures(self):
        from core.pipeline import split_long_beat
        asked = []

        def acquire(number):
            asked.append(number)
            return {"type": "video", "path": f"extra{number}.mp4"}

        shots = split_long_beat(self._shot(30.0), acquire, 12.0)
        self.assertEqual(asked, [2, 3])
        self.assertEqual([item["_media"]["path"] for item in shots], ["first.mp4", "extra2.mp4", "extra3.mp4"])
        self.assertEqual([item["_start"] for item in shots], [100.0, 110.0, 120.0])
        self.assertAlmostEqual(sum(item["_dur"] for item in shots), 30.0)
        self.assertEqual([item["beat_part"] for item in shots], [1, 2, 3])
        self.assertTrue(all(item["narration"] == "anchor" and item["beat_parts"] == 3 for item in shots))

    def test_when_no_more_assets_exist_the_last_picture_fills_the_beat(self):
        from core.pipeline import split_long_beat
        answers = {2: {"type": "image", "path": "extra2.jpg"}, 3: None}
        shots = split_long_beat(self._shot(40.0), lambda number: answers[number], 12.0)
        self.assertEqual(len(shots), 2)
        self.assertEqual(shots[0]["_dur"], 10.0)
        self.assertAlmostEqual(shots[1]["_dur"], 30.0)                 # no gap in the timeline
        self.assertAlmostEqual(shots[1]["_start"] + shots[1]["_dur"], 140.0)

    def test_a_failing_search_does_not_break_the_beat(self):
        from core.pipeline import split_long_beat

        def acquire(number):
            raise OSError("provider down")

        shots = split_long_beat(self._shot(30.0), acquire, 12.0)
        self.assertEqual(len(shots), 1)
        self.assertAlmostEqual(shots[0]["_dur"], 30.0)

    def test_parts_are_capped_and_the_limit_can_be_switched_off(self):
        from core.pipeline import max_shot_seconds, split_long_beat
        shots = split_long_beat(self._shot(120.0), lambda number: {"type": "video", "path": f"{number}.mp4"}, 12.0)
        self.assertEqual(len(shots), 4)
        self.assertAlmostEqual(sum(item["_dur"] for item in shots), 120.0)
        with patch.dict(os.environ, {"LONGFORM_MAX_SHOT_SECONDS": "0"}):
            self.assertEqual(max_shot_seconds(), 0.0)
        with patch.dict(os.environ, {"LONGFORM_MAX_SHOT_SECONDS": "9"}):
            self.assertEqual(max_shot_seconds(), 9.0)
        shot = self._shot(120.0)
        self.assertEqual(split_long_beat(shot, lambda number: self.fail("not needed"), 0.0), [shot])
