import sys, types, unittest
sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))
from core.pipeline import _visual_beats_for_section, _visual_beat_durations

class VisualNarrationAlignmentTests(unittest.TestCase):
    def test_anchor_metadata_is_preserved(self):
        beats = _visual_beats_for_section({"visuals":[{"narration_anchor":"black hole bends light","visual_prompt":"Light curves near a black hole","keywords":["gravitational lensing"]}]})
        self.assertEqual(beats[0]["anchor"], "black hole bends light")
        self.assertEqual(beats[0]["keywords"], ["gravitational lensing"])

    def test_durations_follow_anchor_positions_and_sum_to_chapter(self):
        narration = "First the event horizon appears. Then black hole bends light. Finally the accretion disk glows."
        beats = [{"anchor":"First the event horizon","prompt":"one"},{"anchor":"black hole bends light","prompt":"two"},{"anchor":"accretion disk glows","prompt":"three"}]
        durations = _visual_beat_durations(narration, beats, 90.0)
        self.assertEqual(len(durations), 3)
        self.assertTrue(all(value > 0 for value in durations))
        self.assertAlmostEqual(sum(durations), 90.0, places=6)

    def test_legacy_string_storyboard_remains_supported(self):
        beats = _visual_beats_for_section({"visuals":["A telescope observing Saturn"]})
        self.assertEqual(beats[0]["prompt"], "A telescope observing Saturn")
        self.assertEqual(beats[0]["anchor"], "")

    def test_unmatched_anchors_use_safe_fallback(self):
        beats = [{"anchor":"missing phrase","prompt":"one"},{"anchor":"","prompt":"two"}]
        durations = _visual_beat_durations("A short narration with several words.", beats, 20)
        self.assertAlmostEqual(sum(durations), 20, places=6)

    def test_visuals_are_capped_at_ten_and_empty_prompts_are_ignored(self):
        visuals = [{"visual_prompt": f"Shot {i}", "narration_anchor": f"anchor {i}",
                    "keywords": [f"term {i}"]} for i in range(12)]
        visuals.insert(0, {"visual_prompt": " "})
        beats = _visual_beats_for_section({"visuals": visuals})
        self.assertEqual(len(beats), 10)
        self.assertTrue(all(beat["prompt"] for beat in beats))

    def test_anchor_positions_produce_ordered_positive_shots(self):
        narration = "Alpha begins. Beta follows. Gamma concludes."
        beats = [{"anchor":"Alpha begins","prompt":"a"},
                 {"anchor":"Beta follows","prompt":"b"},
                 {"anchor":"Gamma concludes","prompt":"c"}]
        durations = _visual_beat_durations(narration, beats, 30)
        self.assertTrue(all(d > 0 for d in durations))
        self.assertAlmostEqual(sum(durations), 30)

if __name__ == "__main__":
    unittest.main()
