import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.source_evidence import allocate_evidence_beat_durations, capture_source_screenshot


class SourceScreenshotRetryTests(unittest.TestCase):
    def test_evidence_beat_borrows_time_without_shortening_donor_below_reserve(self):
        beats = [
            {"visual_type": "stock"},
            {"visual_type": "chart", "source_refs": ["S1"]},
            {"visual_type": "stock"},
        ]
        before = [12.0, 0.5, 8.0]
        after = allocate_evidence_beat_durations(beats, before, minimum=4.2, reserve=1.0)
        self.assertAlmostEqual(sum(after), sum(before))
        self.assertGreaterEqual(after[1], 4.2)
        self.assertGreaterEqual(after[0], 1.0)
        self.assertGreaterEqual(after[2], 1.0)

    def test_multiple_evidence_beats_do_not_borrow_from_each_other(self):
        beats = [{"visual_type": "document"}, {"visual_type": "timeline"}, {"visual_type": "stock"}]
        after = allocate_evidence_beat_durations(beats, [0.5, 0.5, 12.0])
        self.assertGreaterEqual(after[0], 4.2)
        self.assertGreaterEqual(after[1], 4.2)
        self.assertGreaterEqual(after[2], 1.0)


    def test_retries_transient_timeout_and_accepts_real_capture_with_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def fake_run(command, *, input, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    raise subprocess.TimeoutExpired(command, 55)
                payload = json.loads(input)
                from PIL import Image
                Image.new("RGB", (1600, 900), "white").save(payload["image_path"], format="PNG")
                Path(payload["metadata_path"]).write_text(
                    json.dumps({"screenshot_is_real_source_page": True, "http_status": 200, "content_validated": True, "page_title": "Official report"}), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="captured", stderr="")

            with patch("core.source_evidence.subprocess.run", side_effect=fake_run), \
                 patch("core.source_evidence.time.sleep"):
                result = capture_source_screenshot(
                    {"source_id": "S5", "url": "https://example.org/report", "publisher": "Example"},
                    directory,
                )

            self.assertEqual(result["status"], "CAPTURED")
            self.assertEqual(len(calls), 2)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertTrue(Path(result["metadata_path"]).is_file())

    def test_rejects_placeholder_page_even_when_a_png_and_sidecar_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(command, *, input, **kwargs):
                payload = json.loads(input)
                from PIL import Image
                Image.new("RGB", (1600, 900), "white").save(payload["image_path"], format="PNG")
                Path(payload["metadata_path"]).write_text(
                    json.dumps({
                        "screenshot_is_real_source_page": True,
                        "http_status": 200,
                        "content_validated": False,
                        "page_title": "Page not found - The National Archives",
                    }), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="captured", stderr="")

            with patch("core.source_evidence.subprocess.run", side_effect=fake_run):
                result = capture_source_screenshot(
                    {"source_id": "S2", "url": "https://example.org/missing", "publisher": "Archive"},
                    directory,
                )
            self.assertEqual(result["status"], "FAILED")
            self.assertIn("validated article/report page", result["reason"])

    def test_does_not_retry_permanent_capture_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            command = ["node", "capture-source.mjs"]
            failed = subprocess.CompletedProcess(command, 1, stdout="", stderr="Refusing a private-network URL")
            with patch("core.source_evidence.subprocess.run", return_value=failed) as run:
                result = capture_source_screenshot(
                    {"source_id": "S5", "url": "https://example.org/report"},
                    directory,
                )
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(run.call_count, 1)
            self.assertIn("private-network", result["reason"])


if __name__ == "__main__":
    unittest.main()
