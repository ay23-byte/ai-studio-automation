"""A long chapter must get enough subject-specific pictures; placeholder "presenter" beats must go.

Production build 43655186698 searched 'documentary presenter studio' for 20 of 38 scenes,
because chapters arrived with too few storyboard beats and were padded with placeholders.
"""
import json
import unittest
from unittest import mock

from core import script_engine

TOPIC = "How the James Webb Space Telescope Sees the First Galaxies"


def _narration(words=420):
    sentence = ("The James Webb Space Telescope collects faint infrared light with a segmented "
                "gold mirror while the sunshield keeps the instruments cold near Lagrange point two. ")
    text = ""
    counter = 0
    while len(text.split()) < words:
        counter += 1
        text += f"Part {counter} begins here. " + sentence
    return text.strip()


def _filler(anchor):
    return {"narration_anchor": anchor, "visual_prompt": "Restrained documentary presenter shot",
            "visual_type": "presenter", "subject": script_engine._FILLER_SUBJECT,
            "keywords": ["documentary presenter"], "search_queries": ["documentary presenter studio"]}


def _real(anchor, kind="stock"):
    return {"narration_anchor": anchor, "visual_prompt": "The gold segmented mirror in a clean room",
            "visual_type": kind, "subject": "James Webb mirror", "keywords": ["webb mirror"],
            "search_queries": ["James Webb mirror"]}


def _model_answer(narration, count):
    words = narration.split()
    beats = []
    for index in range(count):
        start = (index * len(words)) // count
        beats.append({"narration_anchor": " ".join(words[start:start + 5]),
                      "visual_prompt": f"Picture {index + 1} of the telescope hardware",
                      "visual_type": "stock", "subject": "James Webb Space Telescope",
                      "keywords": ["webb telescope"], "search_queries": [f"James Webb telescope view {index + 1}"]})
    return json.dumps({"visuals": beats})


class BeatsNeededTests(unittest.TestCase):
    def test_one_beat_for_about_sixty_words_between_three_and_ten(self):
        self.assertEqual(script_engine.storyboard_beats_needed("word " * 60), 3)
        self.assertEqual(script_engine.storyboard_beats_needed("word " * 420), 7)
        self.assertEqual(script_engine.storyboard_beats_needed("word " * 5000), 10)

    def test_the_word_budget_can_be_changed(self):
        with mock.patch.dict("os.environ", {"LONGFORM_WORDS_PER_VISUAL_BEAT": "42"}):
            self.assertEqual(script_engine.storyboard_beats_needed("word " * 420), 10)


class SubjectPhraseTests(unittest.TestCase):
    def test_the_searchable_subject_is_taken_from_the_topic(self):
        self.assertEqual(script_engine._subject_phrase(TOPIC), "James Webb Space Telescope")


class CompletionTests(unittest.TestCase):
    def _script(self, visuals, words=420):
        return {"title": TOPIC, "sections": [{"title": "The mirror", "narration": _narration(words),
                                              "visuals": visuals}]}

    def test_a_chapter_with_placeholders_gets_a_storyboard_from_the_model(self):
        narration = _narration()
        words = narration.split()
        script = self._script([_filler(" ".join(words[0:5])), _filler(" ".join(words[150:155])),
                               _filler(" ".join(words[300:305]))])
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_model_answer(narration, 7)) as ask:
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        ask.assert_called_once()
        visuals = done["sections"][0]["visuals"]
        self.assertEqual(len(visuals), 7)
        self.assertFalse([beat for beat in visuals if script_engine._is_filler_beat(beat)])
        self.assertFalse([beat for beat in visuals if "presenter" in " ".join(beat["search_queries"]).lower()])
        self.assertEqual(done["storyboard_completion"][0]["source"], "model")
        self.assertEqual(done["storyboard_completion"][0]["placeholders_removed"], 3)

    def test_the_prompt_names_the_topic_the_count_and_forbids_a_presenter(self):
        narration = _narration()
        script = self._script([])
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_model_answer(narration, 7)) as ask:
            script_engine.complete_longform_storyboard(script, TOPIC, "science")
        prompt = ask.call_args[0][0]
        self.assertIn(TOPIC, prompt)
        self.assertIn("exactly 7 visual beats", prompt)
        self.assertIn("Never use a presenter", prompt)

    def test_a_beat_the_model_calls_a_diagram_is_typed_as_the_footage_it_will_be(self):
        import json as _json
        narration = _narration()
        answer = _json.loads(_model_answer(narration, 7))
        answer["visuals"][0]["visual_type"] = "diagram"
        answer["visuals"][1]["visual_type"] = "ai_reconstruction"
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_json.dumps(answer)):
            done = script_engine.complete_longform_storyboard(self._script([]), TOPIC, "science")
        kinds = {beat["visual_type"] for beat in done["sections"][0]["visuals"]}
        self.assertEqual(kinds, {"stock"})
        self.assertEqual(len(done["sections"][0]["visuals"]), 7)

    def test_a_failed_model_call_falls_back_to_beats_made_from_the_narration(self):
        script = self._script([])
        with mock.patch.object(script_engine, "_ask_gemini", side_effect=RuntimeError("quota")):
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        visuals = done["sections"][0]["visuals"]
        self.assertEqual(len(visuals), 7)
        self.assertEqual(done["storyboard_completion"][0]["source"], "narration")
        for beat in visuals:
            self.assertIn("James Webb Space Telescope", " ".join(beat["search_queries"]))
            self.assertNotIn("presenter", beat["visual_prompt"].lower())
            self.assertIn(beat["narration_anchor"], done["sections"][0]["narration"])

    def test_too_few_usable_model_beats_also_fall_back(self):
        narration = _narration()
        script = self._script([])
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_model_answer(narration, 2)):
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        self.assertEqual(done["storyboard_completion"][0]["source"], "narration")
        self.assertEqual(len(done["sections"][0]["visuals"]), 7)

    def test_a_complete_storyboard_is_left_alone_and_the_model_is_not_asked(self):
        narration = _narration()
        words = narration.split()
        beats = [_real(" ".join(words[i * 60:i * 60 + 5])) for i in range(7)]
        script = self._script(beats)
        with mock.patch.object(script_engine, "_ask_gemini") as ask:
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        ask.assert_not_called()
        self.assertEqual(len(done["sections"][0]["visuals"]), 7)
        self.assertEqual(done["storyboard_completion"][0]["source"], "unchanged")

    def test_a_sourced_chart_beat_survives_completion(self):
        narration = _narration()
        words = narration.split()
        chart = _real(" ".join(words[200:205]), kind="chart")
        chart["chart_spec"] = {"title": "Mirror size", "values": [6.5, 2.4]}
        script = self._script([chart, _filler(" ".join(words[300:305]))])
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_model_answer(narration, 7)):
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        kinds = [beat["visual_type"] for beat in done["sections"][0]["visuals"]]
        self.assertIn("chart", kinds)
        self.assertNotIn("presenter", kinds)

    def test_anchors_stay_in_spoken_order_so_the_storyboard_validates(self):
        narration = _narration()
        script = self._script([])
        with mock.patch.object(script_engine, "_ask_gemini", return_value=_model_answer(narration, 7)):
            done = script_engine.complete_longform_storyboard(script, TOPIC, "science")
        script_engine.validate_longform_storyboard(done)
        lowered = done["sections"][0]["narration"].casefold()
        positions, cursor = [], 0
        for beat in done["sections"][0]["visuals"]:
            cursor = lowered.find(beat["narration_anchor"].casefold(), cursor)
            self.assertGreaterEqual(cursor, 0)
            positions.append(cursor)
            cursor += len(beat["narration_anchor"])
        self.assertEqual(positions, sorted(positions))

    def test_a_crash_in_completion_never_loses_the_script(self):
        script = self._script([_real("Part 1 begins here.")] * 3)
        with mock.patch.object(script_engine, "complete_longform_storyboard", side_effect=KeyError("boom")):
            kept = script_engine._complete_storyboard_safely(script, TOPIC, "science")
        self.assertIs(kept, script)
        self.assertEqual(kept["storyboard_completion"][0]["source"], "skipped")

    def test_the_script_provenance_is_stamped_before_the_storyboard_calls(self):
        import inspect
        source = inspect.getsource(script_engine.generate_longform_script)
        self.assertEqual(source.count("_complete_storyboard_safely("), 2)
        for chunk in source.split("_complete_storyboard_safely(")[:-1]:
            self.assertIn("_stamp_generation(script)", chunk.splitlines()[-2])


if __name__ == "__main__":
    unittest.main()
