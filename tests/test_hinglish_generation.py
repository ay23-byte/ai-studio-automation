import json
import unittest
from unittest.mock import patch

from core.content_qa import validate_shorts_script
from core import script_engine


def _script(lines):
    return {
        "title": "Why metals feel colder",
        "hook": "Same room, different feeling",
        "category": "science",
        "scenes": [
            {"say": line, "caption": line, "label": "Science",
             "image": "Close-up material comparison", "keywords": ["metal", "wood"]}
            for line in lines
        ],
    }


class HinglishScriptQualityTests(unittest.TestCase):
    def test_english_only_script_fails_hinglish_qa(self):
        lines = [
            "Metal feels colder because it transfers heat away from your skin much faster than wood does.",
            "Both objects can share the same room temperature, but your hand senses heat flow.",
            "Thermal conductivity explains why the metal surface feels colder when you touch it.",
            "Wood slows heat transfer, so your skin loses energy more slowly than with metal.",
            "The temperature is equal, but the rate of heat transfer changes what you feel.",
        ]
        report = validate_shorts_script(_script(lines), "hinglish")
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("Hinglish" in issue for issue in report["issues"]))

    def test_every_hinglish_scene_must_mix_both_scripts(self):
        lines = [
            "जब metal को touch करते हैं, तो heat जल्दी बाहर जाती है और हाथ को ठंडा लगता है।",
            "दोनों का temperature same हो सकता है, लेकिन metal heat तेज़ी से transfer करता है।",
            "इसीलिए आपकी skin से energy metal में जल्दी जाती है और वह colder लगता है।",
            "लकड़ी heat को धीरे transfer करती है, इसलिए आपका हाथ उतना cold महसूस नहीं करता।",
            "तो असली फर्क temperature नहीं, बल्कि thermal conductivity और heat transfer की speed है।",
        ]
        report = validate_shorts_script(_script(lines), "hinglish")
        self.assertEqual(report["status"], "PASS", report["issues"])

    def test_scene_without_mixed_language_fails_even_if_other_scenes_are_hinglish(self):
        lines = [
            "जब metal को touch करते हैं, तो heat जल्दी बाहर जाती है और हाथ को ठंडा लगता है।",
            "दोनों का temperature same हो सकता है, लेकिन metal heat तेज़ी से transfer करता है।",
            "इसीलिए आपकी skin से energy metal में जल्दी जाती है और वह colder लगता है।",
            "लकड़ी heat को धीरे transfer करती है, इसलिए आपका हाथ उतना cold महसूस नहीं करता।",
            "The temperature is equal, but the rate of heat transfer changes what you feel.",
        ]
        report = validate_shorts_script(_script(lines), "hinglish")
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("scene 5" in issue.lower() for issue in report["issues"]))

    def test_failed_hinglish_generation_uses_specific_corrective_prompt(self):
        english = _script([
            "Metal feels colder because it transfers heat away from your skin much faster than wood does.",
            "Both objects can share the same room temperature, but your hand senses heat flow.",
            "Thermal conductivity explains why the metal surface feels colder when you touch it.",
            "Wood slows heat transfer, so your skin loses energy more slowly than with metal.",
            "The temperature is equal, but the rate of heat transfer changes what you feel.",
        ])
        hinglish = _script([
            "जब metal को touch करते हैं, तो heat जल्दी बाहर जाती है और हाथ को ठंडा लगता है।",
            "दोनों का temperature same हो सकता है, लेकिन metal heat तेज़ी से transfer करता है।",
            "इसीलिए आपकी skin से energy metal में जल्दी जाती है और वह colder लगता है।",
            "लकड़ी heat को धीरे transfer करती है, इसलिए आपका हाथ उतना cold महसूस नहीं करता।",
            "तो असली फर्क temperature नहीं, बल्कि thermal conductivity और heat transfer की speed है।",
        ])
        with patch.dict("os.environ", {"SHORTS_SCRIPT_ATTEMPTS": "2"}), \
             patch.object(script_engine, "_ask_gemini",
                          side_effect=[json.dumps(english, ensure_ascii=False),
                                       json.dumps(hinglish, ensure_ascii=False)]) as ask, \
             patch.object(script_engine, "_stamp_generation"), \
             patch.object(script_engine, "apply_configured_channel_profile",
                          return_value=("Engineering Physics", "", "")):
            result = script_engine.generate_shorts_script(
                "Why do metals feel colder than wood?", category="science", language="hinglish"
            )
        self.assertEqual(result["content_qa"]["status"], "PASS")
        self.assertEqual(ask.call_count, 2)
        self.assertIn("at least two natural Hindi words in Devanagari", ask.call_args_list[1].args[0])
        self.assertIn("Captions must follow the configured subtitle language", ask.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
