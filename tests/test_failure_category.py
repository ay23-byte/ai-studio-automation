"""A failed run names the kind of failure and the next action."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import run_report
from core.failure_category import CATEGORIES, classify_failure

# Real failure texts from production runs and from the code's own error messages.
EXAMPLES = {
    "presenter": [
        "RuntimeError: The 3D presenter render failed (exit 1). The lip-sync model found no speech sounds in chapter 2.",
        "ValueError: Talking Avatar clip for chapter 1 failed A/V validation: Presenter frame rate is unusably low (0.57 fps)",
        "Talking Avatar clip for chapter 3 failed the motion check: Presenter picture is frozen for 1.20s (limit 0.5s).",
    ],
    "asset_shortage": [
        "Review package: 11 of 18 visuals are fallback cards (61%); the limit is 35%.",
        "No relevant footage was found for 7 scenes.",
    ],
    "research": [
        "Research gate: 5 sources were found, 8 are required.",
        "Only 3 publisher domains; 5 are required.",
    ],
    "subtitles": ["Subtitles are not English and were not rendered: 12 subtitle lines contain Devanagari (first: line 4)."],
    "licensing": ["Shot 14 has no usable licence evidence."],
    "configuration": [
        "GEMINI_API_KEY is not set.",
        "GEMINI_API_KEY is required for documentary research and script generation.",
        "minutes must be one of 18, 20, 22, 25, 28 or 30.",
        "The 3D engine packages: three is not installed in avatar_studio/node_modules. Run 'npm ci' first.",
    ],
    "provider_quota": ["Gemini returned HTTP 429 RESOURCE_EXHAUSTED for every model in the chain.",
                       "Pixabay: rate limit reached, Retry-After 3600"],
    "authentication": ["DVIDS answered HTTP 403 for this API key.", "Coverr: 401 Unauthorized"],
    "network": ["ConnectTimeout while contacting images-api.nasa.gov", "GDELT answered HTTP 503"],
    "audio": ["Narration loudness is -31.0 LUFS; the target is -16.", "Edge TTS returned no audio for sentence 41"],
    "rendering": ["ffmpeg exited with code 1 while concatenating segments", "Final video is 1280x720; 1920x1080 is required (resolution)."],
    "editorial_rejection": ["The reviewer rejected shot 9: misleading map.", "Release gate: human approval is missing."],
}


class ClassifyFailureTests(unittest.TestCase):
    def test_examples_land_in_their_category(self):
        for category, messages in EXAMPLES.items():
            for message in messages:
                with self.subTest(message=message):
                    self.assertEqual(classify_failure(message)["category"], category)

    def test_every_category_has_a_label_and_an_action(self):
        for category in list(EXAMPLES) + ["unknown"]:
            label, action = CATEGORIES[category]
            self.assertTrue(label and action.endswith("."))

    def test_empty_or_unrecognised_text_is_unclassified_not_guessed(self):
        self.assertEqual(classify_failure("")["category"], "unknown")
        self.assertEqual(classify_failure(None)["category"], "unknown")
        self.assertEqual(classify_failure("KeyError: 'zzz'")["category"], "unknown")


class RunReportFailureDetailTests(unittest.TestCase):
    """Runs 43605766249, 43588472344, 43572252858 and 43484926451 failed in the review-package
    step and reported only "No failure detail was recorded"."""

    def _collect(self, package_errors):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            build = root / "build" / "job_7"
            (build / "shared_review_package").mkdir(parents=True)
            (build / "shared_review_package" / "package_summary.json").write_text(
                json.dumps({"status": "FAIL", "errors": package_errors}), encoding="utf-8")
            video = root / "out" / "longform_7.mp4"
            video.parent.mkdir()
            video.write_bytes(b"0" * 2048)
            row = {"id": 7, "title": "Reactors", "format": "longform", "status": "done", "language": "hinglish",
                   "category": "science", "output_path": str(video), "error": None, "script_json": "{}"}
            with patch.object(run_report, "find_job", return_value=row), \
                    patch.object(run_report, "_recent_alerts", return_value=[]):
                return run_report.collect("longform", job_status="failure", build_root=root / "build")

    def test_package_blocking_problems_become_the_failure_reason(self):
        report = self._collect(["11 of 18 visuals are fallback cards (61%); the limit is 35%."])
        self.assertIn("11 of 18 visuals are fallback cards", report["failure_reason"])
        self.assertNotIn("No failure detail was recorded", report["failure_reason"])
        self.assertEqual(report["failure_category"], "asset_shortage")
        self.assertIn("Asset shortage", run_report.to_markdown(report))
        self.assertIn("[Asset shortage]", run_report.annotation(report))

    def test_without_any_record_the_report_still_says_so(self):
        report = self._collect([])
        self.assertIn("No failure detail was recorded", report["failure_reason"])
        self.assertEqual(report["failure_category"], "unknown")


if __name__ == "__main__":
    unittest.main()
