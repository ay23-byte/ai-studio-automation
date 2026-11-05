import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import monitoring


class MonitoringTests(unittest.TestCase):
    def test_record_and_summarize_success_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            with patch.object(monitoring, "LOG_PATH", path):
                monitoring.record_event("render", "shorts", "success", 12.5)
                monitoring.record_event("image", "generate", "failure", 3.0, error=RuntimeError("provider down"))
                result = monitoring.summarize_events()
        self.assertEqual(result["total_events"], 2)
        self.assertEqual(result["successes"], 1)
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["failure_components"], {"image": 1})
        self.assertAlmostEqual(result["average_duration_seconds"], 7.75)

    def test_cost_is_not_fabricated(self):
        result = monitoring.configured_cost_estimate()
        self.assertFalse(result["available"])
        self.assertIn("not exposed", result["reason"])

    def test_corrupt_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("not json\n{\"component\":\"render\",\"status\":\"success\"}\n", encoding="utf-8")
            with patch.object(monitoring, "LOG_PATH", path):
                self.assertEqual(len(monitoring.read_events()), 1)


if __name__ == "__main__":
    unittest.main()
