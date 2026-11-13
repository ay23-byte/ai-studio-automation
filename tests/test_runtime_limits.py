"""No media command, download or speech request may run without a limit."""
import ast
import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from core import downloads, ffmpeg_utils, llm, voice

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([*ROOT.glob("core/*.py"), *ROOT.glob("scripts/*.py"),
                  ROOT / "run.py", ROOT / "run_daily.py", ROOT / "dashboard.py"])


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class StaticLimitTests(unittest.TestCase):
    def test_every_subprocess_call_has_a_time_limit(self):
        missing = []
        for path in SOURCES:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = _dotted(node.func)
                if name in {"subprocess.run", "subprocess.check_output", "subprocess.check_call", "subprocess.call"}:
                    keywords = {item.arg for item in node.keywords}
                    if "timeout" not in keywords and None not in keywords:
                        missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(missing, [], "subprocess calls that can hang forever")

    def test_every_http_request_has_a_timeout(self):
        missing = []
        for path in SOURCES:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and _dotted(node.func) in {
                        "requests.get", "requests.post", "requests.head", "requests.put", "requests.request"}:
                    keywords = {item.arg for item in node.keywords}
                    if "timeout" not in keywords and None not in keywords:
                        missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_core_never_hard_codes_the_ffmpeg_binaries_in_a_command(self):
        """FFMPEG_BINARY / FFPROBE_BINARY must reach every command, not only some of them."""
        literal = []
        for path in ROOT.glob("core/*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                        and node.elts[0].value in ("ffmpeg", "ffprobe"):
                    literal.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(literal, [])


class FFmpegTimeoutTests(unittest.TestCase):
    def test_stalled_command_is_killed_and_reported(self):
        command = [sys.executable, "-c", "import time; time.sleep(30)", "out.mp4"]
        with self.assertRaises(ffmpeg_utils.FFmpegTimeout) as caught:
            ffmpeg_utils.run(command, timeout=0.5)
        self.assertIn("FFMPEG_TIMEOUT_SECONDS", str(caught.exception))
        self.assertIn("out.mp4", str(caught.exception))

    def test_default_limit_comes_from_the_environment_and_can_be_disabled(self):
        with patch.dict(os.environ, {"FFMPEG_TIMEOUT_SECONDS": ""}):
            self.assertEqual(ffmpeg_utils.render_timeout(), 3600)
        with patch.dict(os.environ, {"FFMPEG_TIMEOUT_SECONDS": "0.5"}):
            with self.assertRaises(ffmpeg_utils.FFmpegTimeout):
                ffmpeg_utils.run([sys.executable, "-c", "import time; time.sleep(30)"])
        with patch.dict(os.environ, {"FFMPEG_TIMEOUT_SECONDS": "0"}):
            self.assertIsNone(ffmpeg_utils.render_timeout())
        with patch.dict(os.environ, {"FFMPEG_TIMEOUT_SECONDS": "not-a-number"}):
            self.assertEqual(ffmpeg_utils.render_timeout(), 3600)

    def test_failure_still_carries_the_tool_output(self):
        with self.assertRaises(ffmpeg_utils.FFmpegError) as caught:
            ffmpeg_utils.run([sys.executable, "-c", "import sys; sys.stderr.write('bad filter'); sys.exit(3)"])
        self.assertIn("bad filter", str(caught.exception))
        self.assertIsInstance(caught.exception, subprocess.CalledProcessError)


class _Response:
    def __init__(self, chunks, headers=None, status=200, delay=0.0):
        self.chunks, self.headers, self.status, self.delay = chunks, headers or {}, status, delay

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status} Server Error")

    def iter_content(self, _size):
        import time
        for chunk in self.chunks:
            time.sleep(self.delay)
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.target = Path(self._temp.name) / "clip.mp4"

    def fetch(self, response, **env):
        with patch.object(requests, "get", return_value=response), patch.dict(os.environ, env):
            return downloads.fetch("https://example.org/clip.mp4", self.target)

    def assert_nothing_left(self):
        self.assertEqual(list(Path(self._temp.name).iterdir()), [])

    def test_complete_download_is_moved_into_place(self):
        self.fetch(_Response([b"a" * 10, b"b" * 5], {"Content-Length": "15"}))
        self.assertEqual(self.target.read_bytes(), b"a" * 10 + b"b" * 5)
        self.assertEqual([p.name for p in Path(self._temp.name).iterdir()], ["clip.mp4"])

    def test_truncated_transfer_is_rejected(self):
        with self.assertRaisesRegex(downloads.DownloadError, "10 of 15 bytes"):
            self.fetch(_Response([b"a" * 10], {"Content-Length": "15"}))
        self.assert_nothing_left()

    def test_compressed_transfer_is_not_compared_with_the_wire_length(self):
        self.fetch(_Response([b"a" * 40], {"Content-Length": "15", "Content-Encoding": "gzip"}))
        self.assertEqual(self.target.stat().st_size, 40)

    def test_oversized_file_is_refused_before_and_during_the_transfer(self):
        with self.assertRaisesRegex(downloads.DownloadError, "above the 1 MB limit"):
            self.fetch(_Response([b"x"], {"Content-Length": str(5 * 1024 * 1024)}), MAX_VIDEO_DOWNLOAD_MB="1")
        with self.assertRaisesRegex(downloads.DownloadError, "passed the 1 MB limit"):
            self.fetch(_Response([b"x" * 700_000, b"x" * 700_000]), MAX_VIDEO_DOWNLOAD_MB="1")
        self.assert_nothing_left()

    def test_slow_drip_is_stopped_by_the_total_time_limit(self):
        with self.assertRaisesRegex(downloads.DownloadError, "did not finish within"):
            self.fetch(_Response([b"x"] * 50, delay=0.05), MAX_DOWNLOAD_SECONDS="0.1")
        self.assert_nothing_left()

    def test_failure_never_replaces_an_existing_good_file(self):
        self.target.write_bytes(b"good")
        for response in (_Response([b"x"], status=503), _Response([b"x", requests.ConnectionError("reset")]),
                         _Response([])):
            with self.assertRaises(downloads.DownloadError):
                self.fetch(response)
            self.assertEqual(self.target.read_bytes(), b"good")
            self.assertEqual([p.name for p in Path(self._temp.name).iterdir()], ["clip.mp4"])

    def test_every_asset_provider_downloads_through_the_bounded_helper(self):
        for name in ("footage_sources", "images", "archival_images", "openverse_images"):
            text = (ROOT / "core" / f"{name}.py").read_text(encoding="utf-8")
            self.assertIn("from core.downloads import fetch", text, name)
            self.assertNotIn("iter_content", text, name)


class SpeechStallTests(unittest.TestCase):
    def test_stalled_edge_tts_connection_is_abandoned_and_reported(self):
        calls = []

        class Communicate:
            def __init__(self, *args, **kwargs):
                calls.append(args)

            async def save(self, _path):
                await asyncio.Event().wait()          # never completes

        class Provider:
            pass
        Provider.Communicate = Communicate

        async def no_sleep(_seconds):
            return None

        with tempfile.TemporaryDirectory() as temp, patch.object(voice, "_edge_module", return_value=Provider), \
                patch.dict(os.environ, {"EDGE_TTS_TIMEOUT_SECONDS": "0.05"}), \
                patch.object(voice.asyncio, "sleep", no_sleep):
            with self.assertRaisesRegex(RuntimeError, "Edge TTS failed after 3 attempts"):
                asyncio.run(voice._synth_edge("hello", "en-GB-RyanNeural", "+0%", "+0Hz", Path(temp) / "s.mp3"))
        self.assertEqual(len(calls), 3)


class LLMRetryTests(unittest.TestCase):
    def test_json_mode_switch_does_not_use_up_the_retry(self):
        config = {"name": "groq", "base_url": "https://x", "api_key": "k", "model": "m"}
        outcomes = [llm.LLMError("groq", "response_format is not supported", status_code=400, retryable=False),
                    llm.LLMError("groq", "HTTP 503", status_code=503, retryable=True),
                    "ok"]

        def post(*args, **kwargs):
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch.object(llm, "_post_chat", side_effect=post), patch.object(llm.time, "sleep"):
            self.assertEqual(llm.complete(config, "prompt"), "ok")

    def test_retries_stay_bounded(self):
        config = {"name": "groq", "base_url": "https://x", "api_key": "k", "model": "m"}
        with patch.object(llm, "_post_chat",
                          side_effect=llm.LLMError("groq", "HTTP 503", status_code=503)) as post, \
                patch.object(llm.time, "sleep"):
            with self.assertRaises(llm.LLMError):
                llm.complete(config, "prompt", attempts=2)
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
