"""Notification delivery must not depend on optional third-party packages."""
import json
import unittest
from unittest.mock import patch

from core import notify


class NotificationStdlibTests(unittest.TestCase):
    def test_json_payload_uses_stdlib_request(self):
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch("urllib.request.urlopen", return_value=Response()) as open_url:
            self.assertTrue(notify._post("test", "https://example.invalid", json={"text": "ok"}))
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), {"text": "ok"})

    def test_form_payload_is_encoded(self):
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch("urllib.request.urlopen", return_value=Response()) as open_url:
            self.assertTrue(notify._post("test", "https://example.invalid", data={"chat_id": "1", "text": "hello"}))
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertIn(b"chat_id=1", request.data)

    def test_transient_network_error_retries_and_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=OSError("offline")) as open_url, patch("time.sleep"):
            self.assertFalse(notify._post("test", "https://example.invalid", attempts=2))
        self.assertEqual(open_url.call_count, 2)


if __name__ == "__main__":
    unittest.main()
