"""Notification fallback tests: alerts do not depend on optional requests."""
import unittest
from unittest.mock import patch

from core import notify


class NotificationFallbackTests(unittest.TestCase):
    def test_stdlib_fallback_posts_json_and_form_data(self):
        with patch.object(notify, "requests", None), patch.object(
            notify, "_stdlib_post", return_value=(200, {})
        ) as post:
            self.assertTrue(notify._post("test", "https://example.invalid", json={"text": "hello"}))
            post.assert_called_with(
                "https://example.invalid", timeout=20, data=None, json_body={"text": "hello"}
            )
            self.assertTrue(notify._post("test", "https://example.invalid", data={"text": "hello"}))
            post.assert_called_with(
                "https://example.invalid", timeout=20, data={"text": "hello"}, json_body=None
            )

    def test_redacts_webhook_and_telegram_tokens(self):
        message = "bot123:secret https://hooks.slack.com/services/TOKEN discord.com/api/webhooks/abc"
        safe = notify._safe(message)
        self.assertNotIn("secret", safe)
        self.assertNotIn("TOKEN", safe)
        self.assertNotIn("/abc", safe)


if __name__ == "__main__":
    unittest.main()
