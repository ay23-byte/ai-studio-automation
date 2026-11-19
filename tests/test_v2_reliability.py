"""Milestone 2: dependency checks, retries, logging, checkpoints, notifications."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.production_v2.artifacts import ArtifactStore, sha256_file, validate_file
from src.production_v2.checkpoints import CheckpointStore
from src.production_v2.config import V2Config
from src.production_v2.deps import check_dependencies
from src.production_v2.logging import JsonLogger, redact
from src.production_v2.notify import send_failure_notification
from src.production_v2.retry import RetryPolicy, retry


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = V2Config.from_env({})

    def tearDown(self):
        self.tmp.cleanup()

    def test_retry_succeeds_after_transient_failure(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("temporary")
            return "ok"

        self.assertEqual(retry(flaky, policy=RetryPolicy(3, 0, 0, jitter=False)), "ok")
        self.assertEqual(calls["n"], 3)

    def test_retry_stops_after_limit(self):
        calls = {"n": 0}

        def always_fails():
            calls["n"] += 1
            raise TimeoutError("down")

        with self.assertRaises(TimeoutError):
            retry(always_fails, policy=RetryPolicy(2, 0, 0, jitter=False))
        self.assertEqual(calls["n"], 2)

    def test_json_log_redacts_credentials(self):
        path = self.root / "events.jsonl"
        logger = JsonLogger(path, run_id="test")
        logger.info("upload", access_token="super-secret", note="safe")
        record = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(record["fields"]["access_token"], "***REDACTED***")
        self.assertEqual(record["fields"]["note"], "safe")
        self.assertEqual(redact({"api_key": "x", "nested": {"client_secret": "y"}}),
                         {"api_key": "***REDACTED***", "nested": {"client_secret": "***REDACTED***"}})

    def test_dependency_report_has_ffmpeg_and_optional_modules(self):
        report = check_dependencies(self.config)
        self.assertIn("checks", report.as_dict())
        names = {c["name"] for c in report.checks}
        self.assertIn(self.config.ffmpeg_binary, names)
        self.assertIn("python:PIL", names)
        self.assertIn("python:edge_tts", names)

    def test_checkpoint_resume_stage(self):
        store = CheckpointStore(self.root / "checkpoint.json")
        self.assertEqual(store.resume_stage(), "research")
        store.done("research", {"sources": 3})
        self.assertTrue(store.is_done("research"))
        self.assertEqual(store.resume_stage(), "script")
        store.failed("script", "bad script")
        self.assertIn("script", store.data["failed"])

    def test_artifact_manifest_contains_checksums(self):
        store = ArtifactStore(self.root, "run-1")
        store.write_text("a.txt", "hello")
        store.write_json("b.json", {"ok": True})
        manifest = store.finalize_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        paths = {f["path"] for f in data["files"]}
        self.assertEqual(paths, {"a.txt", "b.json"})
        digest = sha256_file(store.path("a.txt"))
        self.assertTrue(any(f["sha256"] == digest for f in data["files"]))
        self.assertEqual(validate_file(store.path("a.txt"), min_bytes=1)["size_bytes"], 5)
        with self.assertRaises(Exception):
            validate_file(store.path("missing.txt"))

    def test_notification_uses_injected_transport_and_never_leaks_token(self):
        sent = []
        config = V2Config.from_env({"PV2_FAILURE_WEBHOOK": "https://example.invalid/hook"})
        config.failure_webhook = "https://example.invalid/hook"
        ok = send_failure_notification("FAILED", {"run_id": "r1", "access_token": "secret"},
                                       config, transport=lambda url, data: sent.append((url, data)))
        self.assertTrue(ok)
        self.assertNotIn(b"secret", sent[0][1])


if __name__ == "__main__":
    unittest.main()
