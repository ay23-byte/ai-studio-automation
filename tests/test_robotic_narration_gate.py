"""A documentary never continues with the robotic fallback voice.

Production build 43702915253: Edge TTS answered no voice, the narration fell back to espeak-ng,
and the build rendered for two hours before audio QA refused the film for that voice.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import pipeline
from core.failure_category import classify_failure

REASON = ('All Edge TTS voices failed: hi-IN-MadhurNeural stopped at phrase 37/104 "Lekin yahan ek twist hai" '
          '(NoAudioReceived: No audio was received. Please verify that your para); en-IN-PrabhatNeural stopped '
          'at phrase 37/104 "Lekin yahan ek twist hai" (NoAudioReceived: No audio was received. Please verify '
          'that your para); using offline espeak-ng.')


class FakeCheckpoint:
    def run(self, stage, fn, **_):
        return fn()


def write_report(folder, **values):
    report = {"requested_engine": "edge", "actual_engine": "edge", "fallback_reason": None,
              "voice": "hi-IN-MadhurNeural", "language": "hinglish"}
    report.update(values)
    (Path(folder) / "narration_synthesis_report.json").write_text(json.dumps(report), encoding="utf-8")


class RoboticNarrationGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.build = Path(tmp.name)

    def stage(self, **kwargs):
        def fake_synthesise(text, bd, language, gender):
            (Path(bd) / "voice.wav").write_bytes(b"RIFF")
            return Path(bd) / "voice.wav", [(0.0, 1.0, text)]
        with patch.object(pipeline, "synthesise", fake_synthesise):
            return pipeline._voice_stage(FakeCheckpoint(), "Ek vakya.", self.build, "hinglish", "male", **kwargs)

    def test_a_natural_voice_passes(self):
        write_report(self.build)
        self.assertIsNone(pipeline.robotic_narration_problem(self.build))
        path, timings = self.stage(refuse_robotic=True)
        self.assertEqual(len(timings), 1)

    def test_another_natural_voice_is_not_refused(self):
        write_report(self.build, voice="en-IN-PrabhatNeural", fallback_reason=None)
        self.assertIsNone(pipeline.robotic_narration_problem(self.build))

    def test_the_robotic_fallback_stops_the_documentary_at_the_narration(self):
        write_report(self.build, actual_engine="espeak-ng", voice="espeak-ng offline fallback", fallback_reason=REASON)
        with self.assertRaises(RuntimeError) as caught:
            self.stage(refuse_robotic=True)
        message = str(caught.exception)
        self.assertTrue(message.startswith("Narration failed, no natural voice was available:"))
        # The phrase and the cause for each voice are inside the first 400 characters, the
        # length the run page shows.
        self.assertIn('en-IN-PrabhatNeural stopped at phrase 37/104', message[:400])
        self.assertIn("NoAudioReceived", message[:400])
        self.assertIn("espeak-ng", message)
        # The timings file is not written, so nothing later can use this narration.
        self.assertFalse((self.build / "voice_timings.json").exists())
        self.assertEqual(classify_failure(message)["category"], "audio")

    def test_an_offline_voice_that_was_asked_for_is_not_a_fallback(self):
        write_report(self.build, requested_engine="espeak-ng", actual_engine="espeak-ng")
        self.assertIsNone(pipeline.robotic_narration_problem(self.build))

    def test_short_formats_keep_the_fallback(self):
        write_report(self.build, actual_engine="espeak-ng", fallback_reason=REASON)
        path, timings = self.stage()                      # refuse_robotic is off by default
        self.assertEqual(len(timings), 1)

    def test_the_gate_can_be_switched_off(self):
        write_report(self.build, actual_engine="espeak-ng", fallback_reason=REASON)
        with patch.dict("os.environ", {"LONGFORM_ALLOW_ROBOTIC_NARRATION": "1"}):
            self.assertIsNone(pipeline.robotic_narration_problem(self.build))

    def test_the_documentary_build_asks_for_the_gate(self):
        source = Path(pipeline.__file__).read_text(encoding="utf-8")
        body = source[source.index("def build_longform("):]
        self.assertIn("refuse_robotic=True", body)


if __name__ == "__main__":
    unittest.main()
