import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import voice


class VoiceFallbackTests(unittest.TestCase):
    def test_edge_outage_uses_offline_espeak_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            generated = [build_dir / "s0.wav"]

            def fake_espeak(sentences, language, folder):
                generated[0].write_bytes(b"RIFF" + b"\x00" * 128)
                return generated

            def fake_join(wavs, pauses, output, speed=1.0):
                output.write_bytes(b"RIFF" + b"\x00" * 128)

            with patch.dict("os.environ", {"TTS_ENGINE": "edge", "CI": "true", "GITHUB_ACTIONS": "false", "TTS_REJECT_ROBOTIC_FALLBACK": "0"}), \
                 patch.object(voice, "_synth_all_edge", side_effect=RuntimeError("NoAudioReceived")), \
                 patch.object(voice, "_synth_all_espeak", side_effect=fake_espeak) as offline, \
                 patch.object(voice, "_dur", return_value=1.0), \
                 patch.object(voice, "_join", side_effect=fake_join):
                output, timings = voice._synthesise_raw("Chernobyl changed history.", build_dir, language="english")

            self.assertTrue(output.exists())
            self.assertEqual(len(timings), 1)
            offline.assert_called_once()
            report = json.loads((build_dir / "narration_synthesis_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["actual_engine"], "espeak-ng")
            self.assertIn("NoAudioReceived", report["fallback_reason"])



    def test_github_actions_rejects_robotic_fallback_by_default(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {"TTS_ENGINE": "edge", "GITHUB_ACTIONS": "true"}, clear=True), \
             patch.object(voice, "_synth_all_edge", side_effect=RuntimeError("NoAudioReceived")), \
             patch.object(voice, "_synth_all_espeak", return_value=[Path(tmp) / "s0.wav"]) as offline:
            with self.assertRaisesRegex(voice.VoiceProviderUnavailable, "Production narration refused"):
                voice._synthesise_raw("A clear test sentence.", Path(tmp), language="english")
            offline.assert_called_once()

    def test_required_chatterbox_failure_blocks_edge_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict("os.environ", {
                 "TTS_ENGINE": "chatterbox",
                 "TTS_REQUIRE_ENGINE": "chatterbox",
                 "ALLOW_CHATTERBOX_ON_CI": "true",
                 "CI": "true",
             }), \
             patch.object(voice, "_synth_all_chatterbox", side_effect=RuntimeError("model unavailable")), \
             patch.object(voice, "_synth_all_edge") as edge:
            with self.assertRaisesRegex(voice.VoiceProviderUnavailable, "Required narration engine 'chatterbox' failed"):
                voice._synthesise_raw("Physics explains the world.", Path(tmp), language="english")
            edge.assert_not_called()

    def test_edge_voice_candidates_include_independent_providers_voices(self):
        with patch.dict("os.environ", {"TTS_VOICE": "en-IN-NeerjaNeural"}):
            voices = voice._edge_voice_candidates("english", "female")
        self.assertEqual(voices[0], "en-IN-NeerjaNeural")
        self.assertGreaterEqual(len(voices), 3)
        self.assertEqual(len(voices), len(set(voices)))


if __name__ == "__main__":
    unittest.main()
