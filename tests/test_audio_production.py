import tempfile
import unittest
from pathlib import Path

from core.audio_production import (
    music_profile, narration_cleanup_filter, write_audio_production_report,
)


class AudioProductionTests(unittest.TestCase):
    def test_denoising_is_gentle_and_keeps_voice_band(self):
        value = narration_cleanup_filter()
        self.assertIn("highpass=f=75", value)
        self.assertIn("lowpass=f=14500", value)
        self.assertIn("afftdn=nf=-28", value)
        self.assertIn("aresample=48000", value)

    def test_topic_selects_distinct_music_moods(self):
        self.assertEqual(music_profile("Quantum physics discovery", "shorts")["mood"], "discovery")
        self.assertEqual(music_profile("Breaking election policy", "news")["mood"], "newsroom")
        self.assertEqual(music_profile("Mystery of an ancient war", "documentary")["mood"], "investigative")

    def test_pipeline_visual_identity_is_fixed(self):
        self.assertEqual(music_profile("", "shorts")["pipeline_style"], "high-energy cinematic explainer")
        self.assertEqual(music_profile("", "news")["pipeline_style"], "modern newsroom visual journalism")
        self.assertEqual(music_profile("", "documentary")["pipeline_style"], "cinematic investigative documentary")

    def test_music_arrangement_is_layered_and_topic_matched(self):
        profile = music_profile("The mystery of India's disappearing villages", "documentary")
        self.assertEqual(profile["mood"], "investigative")
        self.assertGreaterEqual(len(profile["notes_hz"]), 4)
        self.assertGreater(profile["tempo_bpm"], 0)
        self.assertIn("soft_low_pulse", profile["arrangement"])
        self.assertIn("gentle_reverb", profile["arrangement"])

    def test_music_profiles_do_not_share_one_generic_tempo_or_bass(self):
        discovery = music_profile("Quantum physics and space", "shorts")
        newsroom = music_profile("Breaking economy news", "news")
        documentary = music_profile("History of a village", "documentary")
        self.assertNotEqual(discovery["tempo_bpm"], newsroom["tempo_bpm"])
        self.assertNotEqual(newsroom["bass_hz"], documentary["bass_hz"])

    def test_audio_report_keeps_human_review_and_licence_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = music_profile("space science", "shorts")
            report = write_audio_production_report(temp, profile, "original_generated",
                                                   mix_settings={"final_sample_rate_hz": 48000})
            self.assertTrue(report["narration_cleanup"]["enabled"])
            self.assertEqual(report["narration_cleanup"]["sample_rate_hz"], 48000)
            self.assertEqual(report["mix_policy"]["final_sample_rate_hz"], 48000)
            self.assertTrue(report["mix_policy"]["human_listening_review_required"])
            self.assertEqual(report["music"]["licensing"], "original_synthesised_instrumental")
            self.assertTrue((Path(temp) / "audio_production_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
