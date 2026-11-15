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

    def test_narration_line_gives_measured_words_minutes_and_rate(self):
        self._write("runtime_report.json", {"target_minutes": 18, "narration_minutes": 16.4, "words": 2610,
                                            "words_per_minute": 159.1, "fraction_of_target": 0.9111,
                                            "minimum_fraction": 0.8, "status": "PASS"})
        self._write("narration_synthesis_report.json", {"chunk_count": 105, "speech_seconds": 952.0,
                                                        "inserted_pause_seconds": 27.3, "characters_per_second": 15.2})
        line = evidence.narration(self.build)
        self.assertIn("2610 words spoken in 16.4 min (159.1 words a minute)", line)
        self.assertIn("91% of the requested 18 min, floor 80%: PASS", line)
        self.assertIn("105 phrases, 952.0 s of speech + 27.3 s of pauses", line)
        self.assertIn("15.2 characters a second", line)

    def test_scenes_without_footage_show_their_searches_and_reasons(self):
        self._write("provider_report.json", {"scenes": [
            {"scene": "chapter 1 beat 2", "status": "needs_manual_review", "candidates_found": 4,
             "candidates_eligible": 0, "reason": "no candidate met the relevance gate",
             "queries": ["Webb sunshield deployment", "JWST sunshield", "tennis court sized sunshield", "x"]},
            {"scene": "chapter 2 beat 1", "status": "needs_manual_review", "candidates_found": 0,
             "candidates_eligible": 0, "reason": "no provider returned a candidate for these queries",
             "queries": ["Ariane 5 launch Kourou"]},
            {"scene": "chapter 2 beat 2", "status": "selected", "queries": ["a"]}]})
        line = evidence.scenes_without_footage(self.build)
        self.assertIn("2 scenes got no footage", line)
        self.assertIn("no candidate met the relevance gate x1", line)
        self.assertIn("chapter 1 beat 2: searched 'Webb sunshield deployment', 'JWST sunshield', "
                      "'tennis court sized sunshield' (4 found, 0 eligible)", line)
        self.assertNotIn("'x'", line)
        self.assertIn("chapter 2 beat 1: searched 'Ariane 5 launch Kourou' (0 found, 0 eligible)", line)

    def test_storyboard_and_picture_audit_ride_on_existing_lines(self):
        self._write("script.json", {
            "sections": [{"visuals": [{"visual_type": "stock"}] * 5 + [{"visual_type": "diagram"}] * 2},
                         {"visuals": [{"visual_type": "archival"}] * 6}],
            "storyboard_completion": [{"chapter": 1, "source": "model", "placeholders_removed": 3},
                                      {"chapter": 2, "source": "unchanged", "placeholders_removed": 0}]})
        self._write("visual_consistency_report.json", {
            "status": "FAIL", "summary": {"pass_count": 30, "review_count": 4, "fail_count": 1, "unavailable_count": 0},
            "shots": [{"shot_id": "shot_0012", "status": "fail",
                       "issues": ["Sampled visual is 480×360, below the 640×360 source resolution floor."]}]})
        plan = evidence.storyboard(self.build)
        self.assertIn("storyboard 13 beats in 2 chapters (7, 6)", plan)
        self.assertIn("stock x5", plan)
        self.assertIn("model x1", plan)
        self.assertIn("3 placeholder beats removed", plan)
        audit = evidence.visual_checks(self.build)
        self.assertIn("picture audit FAIL: 30 pass, 4 review, 1 fail", audit)
        self.assertIn("shot_0012 Sampled visual is 480×360", audit)
        report = {"output": {"exists": True, "file": "longform_7.mp4", "duration_seconds": 1188.0},
                  "text_model": {"provider": "gemini", "model": "m"}}
        lines = evidence.evidence_lines(report, self.build, 18)
        self.assertLessEqual(len(lines), evidence.MAX_NOTICES)
        self.assertTrue(any(line.startswith("Script and narration:") and "storyboard 13 beats" in line
                            for line in lines))
        self.assertTrue(any(line.startswith("Visuals:") and "picture audit FAIL" in line for line in lines))
        self._write("picture_repair_report.json", {"replaced": 8, "unresolved": 0, "shots": [{"shot": 36}]})
        lines = evidence.evidence_lines(report, self.build, 18)
        self.assertTrue(any("8 unusable pictures replaced before the edit, 0 not replaceable" in line
                            for line in lines))

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
