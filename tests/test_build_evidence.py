"""The run-page evidence notices state only what the build recorded."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts import annotate_build_evidence as evidence


class BuildEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build = Path(self.temp.name)

    def _write(self, name, data):
        (self.build / name).write_text(json.dumps(data), encoding="utf-8")

    def test_final_video_line_compares_measured_runtime_with_the_request(self):
        report = {"output": {"exists": True, "file": "longform_7.mp4", "duration_seconds": 1188.0, "width": 1920,
                             "height": 1080, "video_codec": "h264", "audio_codec": "aac", "size_mb": 412.5}}
        line = evidence.final_video(report, 22)
        self.assertIn("19 min 48 s", line)
        self.assertIn("90% of the requested 22 minutes", line)
        self.assertIn("1920x1080", line)
        self.assertIn("h264/aac", line)

    def test_a_missing_video_is_stated_not_hidden(self):
        self.assertEqual(evidence.final_video({"output": {"exists": False}}, 22),
                         "Final video: no finished file was produced.")

    def test_voice_fallback_is_named(self):
        line = evidence.script_and_voice({
            "text_model": {"provider": "gemini", "model": "gemini-2.5-flash", "fallback_used": True},
            "voice": {"provider": "edge", "voice": "en-IN-PrabhatNeural", "status": "fallback",
                      "requested_provider": "kokoro", "fallback_reason": "model download failed"}})
        self.assertIn("(fallback model)", line)
        self.assertIn("FALLBACK from kokoro: model download failed", line)

    def test_visual_line_counts_shot_kinds_and_fallback_cards(self):
        self._write("visual_review_report.json", {"shots": [
            {"presenter_mode": "talking_avatar"},
            {"visual_type": "stock", "media_type": "video"}, {"visual_type": "stock", "media_type": "video"},
            {"visual_type": "context_graphic", "media_type": "image"}]})
        line = evidence.visuals({"visuals": {"providers": {"NASA": 2}, "fallback_card_count": 1,
                                             "fallback_card_share": 0.3333}}, self.build)
        self.assertIn("4 shots: stock (video) x2", line)
        self.assertIn("presenter x1", line)
        self.assertIn("sources: NASA x2", line)
        self.assertIn("1 fallback cards (33% of visuals)", line)

    def test_footage_line_gives_each_source_and_its_main_refusal(self):
        self._write("provider_report.json", {
            "summary": {"scenes_searched": 40, "scenes_with_footage": 22},
            "providers": [
                {"provider": "NASA", "searches": 30, "results_found": 210, "download_successes": 12, "selected_scenes": 10,
                 "rejection_reasons": {"low relevance": 150, "too short": 4}},
                {"provider": "DVIDS", "searches": 2, "results_found": 0, "download_successes": 0, "selected_scenes": 0,
                 "disabled_reason": "HTTP 403 on every request; skipped for the rest of this run"},
                {"provider": "Mixkit", "searches": 0}]})
        line = evidence.footage_sources(self.build)
        self.assertIn("22 of 40 searched scenes got footage", line)
        self.assertIn("NASA: 30 searches, 210 results, 12 valid downloads, 10 used, most refused for 'low relevance' x150", line)
        self.assertIn("DVIDS: 2 searches", line)
        self.assertIn("problem: HTTP 403", line)
        self.assertNotIn("Mixkit", line)                    # never asked: not listed

    def test_presenter_line_reports_the_worst_measured_clip(self):
        self._write("visual_review_report.json", {"shots": [
            {"presenter_mode": "talking_avatar", "presenter_motion": {
                "status": "PASS", "distinct_frame_rate": 29.9, "longest_frozen_seconds": 0.03,
                "voiced_mouth_motion_ratio": 0.98, "mouth_track_offset_frames": 0}},
            {"presenter_mode": "talking_avatar", "presenter_motion": {
                "status": "PASS", "distinct_frame_rate": 24.5, "longest_frozen_seconds": 0.2,
                "voiced_mouth_motion_ratio": 0.81, "mouth_track_offset_frames": -1}},
            {"visual_type": "stock"}]})
        line = evidence.presenter(self.build)
        self.assertIn("2 clips (talking_avatar)", line)
        self.assertIn("2 of 2 passed the motion check", line)
        self.assertIn("lowest 24.5 different frames per second", line)
        self.assertIn("longest freeze 0.20 s", line)
        self.assertIn("at least 81% of voiced frames", line)
        self.assertIn("largest mouth-to-audio offset 1 frames", line)
        self.assertIn("human review of lip-sync pending", line)

    def test_presenter_without_measurement_says_so(self):
        self._write("visual_review_report.json", {"shots": [{"presenter_mode": "still_cpu"}]})
        self.assertIn("motion was not measured", evidence.presenter(self.build))

    def test_subtitle_audio_and_research_lines(self):
        self._write("subtitle_translation.json", {"target": "english", "mode": "translated", "english_check": {
            "status": "PASS", "lines": 310, "devanagari_lines": 0, "roman_hindi_lines": 0}})
        self._write("audio_quality_report.json", {"status": "PASS", "human_review_status": "pending", "metrics": {
            "integrated_lufs": -16.2, "true_peak_dbfs": -1.4, "loudness_range_lu": 6.1, "clipped_samples": 0,
            "long_silence_count": 0}})
        self._write("research_verification_report.json", {
            "sources": [{"url_valid": True}, {"url_valid": True}, {"url_valid": False}],
            "claims": [{"traceability_status": "source_linked_pending_human_review"},
                       {"traceability_status": "needs_verification"}]})
        self.assertIn("English check PASS on 310 lines (0 Devanagari, 0 Hindi in Latin letters)",
                      evidence.subtitles(self.build))
        sound = evidence.audio(self.build)
        self.assertIn("loudness -16.2 LUFS", sound)
        self.assertIn("true peak -1.4 dBTP", sound)
        self.assertIn("0 clipped samples", sound)
        found = evidence.research(self.build)
        self.assertIn("3 sources (2 with a valid URL)", found)
        self.assertIn("2 claims, 1 linked to a source", found)
        self.assertIn("fact check by a person still required", found)

    def test_missing_reports_produce_no_line_and_long_lines_are_cut(self):
        self.assertEqual(evidence.evidence_lines({"output": {"exists": False}}, self.build, None),
                         ["Final video: no finished file was produced."])
        self.assertLessEqual(len(evidence._clean("x" * 5000)), evidence.MAX_MESSAGE)
        self.assertNotIn("::", evidence._clean("a::b"))

    def test_review_report_carries_the_presenter_measurements(self):
        text = (Path(__file__).resolve().parent.parent / "core/visual_production.py").read_text(encoding="utf-8")
        self.assertIn('"presenter_motion": shot.get("presenter_motion")', text)


if __name__ == "__main__":
    unittest.main()
