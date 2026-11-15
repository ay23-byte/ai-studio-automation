"""A script that cannot be written says why.

Production build 43716476390 ended after three minutes with "The run stopped before a job was
created; see the failing step." No job exists while the script is written, so the cause was
recorded nowhere.
"""
import argparse
import unittest
from unittest.mock import patch

import run
from core import notify


def long_args(**values):
    base = dict(topic="How telescopes work", category="science", minutes=18, language="hinglish",
                voice_gender="male", demo=False, force=True, upload=False, subtitle_language=None)
    base.update(values)
    return argparse.Namespace(**base)


class ScriptFailureAlertTests(unittest.TestCase):
    def test_a_failed_longform_script_sends_the_cause_and_still_fails(self):
        error = RuntimeError("Gemini rate limit or quota reached for gemini-3.1-flash-lite")
        with patch.object(run, "generate_longform_script", side_effect=error), \
             patch.object(run, "save_video") as save, patch.object(run, "send") as send:
            with self.assertRaises(RuntimeError):
                run.cmd_long(long_args())
        save.assert_not_called()                                   # no job was created
        message = send.call_args.args[0]
        self.assertTrue(message.startswith("❌ Long-form script failed, nothing was built: RuntimeError: "))
        self.assertIn("quota reached for gemini-3.1-flash-lite", message)
        self.assertEqual(notify._level(message), "error")          # the run report quotes error alerts

    def test_a_failed_shorts_script_sends_the_cause(self):
        args = argparse.Namespace(topic="Inflation", category="finance", language="english",
                                  voice_gender="male", demo=False, force=True, upload=False)
        with patch.object(run, "generate_shorts_script", side_effect=ValueError("script too short")), \
             patch.object(run, "send") as send:
            with self.assertRaises(ValueError):
                run.cmd_shorts(args)
        self.assertIn("Shorts script failed, nothing was built: ValueError: script too short",
                      send.call_args.args[0])

    def test_a_script_that_is_written_sends_nothing(self):
        with patch.object(run, "send") as send:
            self.assertEqual(run._script_or_alert("Long-form", lambda: {"title": "ok"}), {"title": "ok"})
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
