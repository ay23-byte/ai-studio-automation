import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.source_evidence import capture_source_screenshot


class SourceScreenshotRetryTests(unittest.TestCase):
    def test_retries_transient_timeout_and_accepts_real_capture_with_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def fake_run(command, *, input, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    raise subprocess.TimeoutExpired(command, 55)
                payload = json.loads(input)
                from PIL import Image\n                Image.new("RGB", (1600, 900), "white").save(payload["image_path"], format="PNG")
                Path(payload["metadata_path"]).write_text(
                    json.dumps({"screenshot_is_real_source_page": True}), encoding="utf-8"
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
