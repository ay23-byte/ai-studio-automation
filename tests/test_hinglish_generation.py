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


class LongformHinglishQualityTests(unittest.TestCase):
    def test_english_only_documentary_is_rejected_when_hinglish_was_requested(self):
        script = {"sections": [
            {"title": "Origins", "narration": " ".join(
                ["The Titanic was a large passenger ship built in Belfast."] * 30)},
            {"title": "Voyage", "narration": " ".join(
                ["The ship departed Southampton and crossed the North Atlantic."] * 30)},
        ]}
        with self.assertRaisesRegex(ValueError, "does not match the requested hinglish style"):
            script_engine.validate_longform_language(script, "hinglish")

    def test_natural_hinglish_is_checked_in_each_chapter(self):
        mixed = " ".join(
            ["जब metal गरम होता है, तो उसकी particles की motion तेज़ हो जाती है।"] * 35
        )
        script = {"sections": [
            {"title": "Origins", "narration": mixed},
            {"title": "Mechanism", "narration": mixed},
        ]}
        report = script_engine.validate_longform_language(script, "hinglish")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(script["language"], "hinglish")
        self.assertTrue(all(item["hindi_words"] > 0 and item["english_words"] > 0
                            for item in report["sections"]))

    def test_one_english_only_chapter_fails_even_when_other_chapters_are_hinglish(self):
        mixed = " ".join(
            ["जब metal गरम होता है, तो उसकी particles की motion तेज़ हो जाती है।"] * 35
        )
        english = " ".join(["The ship crossed the North Atlantic during its voyage."] * 35)
        script = {"sections": [
            {"title": "Origins", "narration": mixed},
            {"title": "Voyage", "narration": english},
        ]}
        with self.assertRaisesRegex(ValueError, "Chapter 2 is not genuinely mixed-language"):
            script_engine.validate_longform_language(script, "hinglish")

    def test_longform_language_failure_uses_configured_groq_repair(self):
        import json
        from core import llm

        mixed = " ".join(
            ["जब quantum system की energy बदलती है, तो उसका measurable state भी change होता है।"] * 35
        )
        repaired = {"title": "Quantum systems", "sections": [
            {"title": "Origins", "narration": mixed},
            {"title": "Mechanism", "narration": mixed},
        ]}
        provider = {"name": "groq", "model": "llama-test", "base_url": "https://example.test", "api_key": "test"}
        with patch.object(llm, "configured_providers", return_value=[provider]), \\
             patch.object(llm, "complete", return_value=json.dumps(repaired, ensure_ascii=False)) as complete:
            result, repair_prompt = script_engine._try_longform_language_fallback(
                "Original documentary prompt", "hinglish", "science", [], 
                ValueError("English-only chapter")
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["language_quality"]["status"], "PASS")
        self.assertEqual(result["generation"]["provider"], "groq") if "generation" in result else None
        self.assertEqual(complete.call_args.args[0]["name"], "groq")
        self.assertIn("LANGUAGE REPAIR REQUIRED", repair_prompt)
        self.assertIn("every chapter", repair_prompt.lower())

    def test_english_and_hindi_language_modes_are_also_enforced(self):
        english = {"sections": [{"narration": "The ship crossed the North Atlantic."}]}
        self.assertEqual(script_engine.validate_longform_language(english, "english")["status"], "PASS")
        hindi = {"sections": [{"narration": " ".join(["जहाज समुद्र में आगे बढ़ रहा था।"] * 20)}]}
        self.assertEqual(script_engine.validate_longform_language(hindi, "hindi")["status"], "PASS")


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
