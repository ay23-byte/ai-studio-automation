"""The speaker must not change gender, and a short outage must not change the speaker.

Production build 43691314957 asked for a male voice and was narrated by hi-IN-SwaraNeural (a
female voice) next to a male presenter: one phrase failed and the fallback list held a female voice.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import voice

FEMALE = {"hi-IN-SwaraNeural", "hi-IN-AnanyaNeural", "en-IN-NeerjaNeural", "en-US-AriaNeural", "en-GB-SoniaNeural"}
MALE = {"hi-IN-MadhurNeural", "en-IN-PrabhatNeural", "en-US-GuyNeural", "en-GB-RyanNeural"}


class CandidateTests(unittest.TestCase):
    def test_no_fallback_voice_has_the_other_gender(self):
        with patch.dict("os.environ", {"TTS_VOICE": ""}):
            for language in ("hindi", "hinglish", "english"):
                with self.subTest(language=language):
                    self.assertFalse(set(voice._edge_voice_candidates(language, "male")) & FEMALE)
                    self.assertFalse(set(voice._edge_voice_candidates(language, "female")) & MALE)

    def test_the_configured_voice_is_tried_first_and_alternatives_exist(self):
        with patch.dict("os.environ", {"TTS_VOICE": ""}):
            self.assertEqual(voice._edge_voice_candidates("hinglish", "male")[0], voice.pick_voice("hinglish", "male"))
            for language in ("hindi", "hinglish", "english"):
                for gender in ("male", "female"):
                    candidates = voice._edge_voice_candidates(language, gender)
                    self.assertGreaterEqual(len(candidates), 2)
                    self.assertEqual(len(candidates), len(set(candidates)))


class SameVoiceRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build = Path(self.temp.name)
        self.calls = []

    def run_edge(self, failures, sentences=("one.", "two.", "three.", "four.")):
        """``failures``: {(voice, phrase index): how many times it fails}."""
        remaining = dict(failures)

        async def fake_synth(text, voice_name, rate, pitch, path):
            index = int(Path(path).stem[1:])
            self.calls.append((voice_name, index))
            if remaining.get((voice_name, index), 0) > 0:
                remaining[(voice_name, index)] -= 1
                raise RuntimeError("NoAudioReceived")
            Path(path).write_bytes(b"mp3")

        def fake_wav(mp3, wav):
            Path(wav).write_bytes(b"RIFF" + b"\x00" * 64)

        with patch.dict("os.environ", {"TTS_VOICE": ""}), \
             patch.object(voice, "_edge_module", return_value=object()), \
             patch.object(voice, "_synth_edge", fake_synth), patch.object(voice, "_to_wav", fake_wav), \
             patch("time.sleep") as sleep:
            wavs = voice._synth_all_edge(list(sentences), "hinglish", "male", self.build)
        return wavs, sleep

    def test_a_failed_phrase_is_retried_with_the_same_voice_and_earlier_phrases_are_kept(self):
        wavs, sleep = self.run_edge({("hi-IN-MadhurNeural", 2): 1})
        self.assertEqual(len(wavs), 4)
        self.assertEqual(voice._LAST_EDGE_VOICE, "hi-IN-MadhurNeural")
        self.assertEqual({name for name, _ in self.calls}, {"hi-IN-MadhurNeural"})
        self.assertEqual([index for _, index in self.calls], [0, 1, 2, 2, 3])     # phrases 1 and 2 spoken once
        sleep.assert_called_once_with(20.0)

    def test_the_wait_doubles_and_can_be_configured(self):
        with patch.dict("os.environ", {"EDGE_TTS_VOICE_RETRY_SECONDS": "5"}):
            self.assertEqual([voice._voice_retry_wait(n) for n in (1, 2, 3)], [5.0, 10.0, 20.0])
        with patch.dict("os.environ", {"EDGE_TTS_VOICE_PASSES": "5"}):
            self.assertEqual(voice._same_voice_passes(), 5)

    def test_a_voice_that_keeps_failing_is_replaced_by_one_of_the_same_gender(self):
        wavs, sleep = self.run_edge({("hi-IN-MadhurNeural", 1): 99})
        self.assertEqual(len(wavs), 4)
        self.assertEqual(voice._LAST_EDGE_VOICE, "en-IN-PrabhatNeural")
        self.assertNotIn(voice._LAST_EDGE_VOICE, FEMALE)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [20.0, 40.0, 80.0])   # four tries
        # The narration is one voice from start to finish: every phrase is spoken again.
        self.assertEqual([index for name, index in self.calls if name == "en-IN-PrabhatNeural"], [0, 1, 2, 3])

    def test_when_no_voice_of_that_gender_works_the_narration_fails_instead_of_switching_gender(self):
        failures = {(name, 0): 99 for name in ("hi-IN-MadhurNeural", "en-IN-PrabhatNeural")}
        with self.assertRaises(RuntimeError) as caught:
            self.run_edge(failures)
        self.assertIn("All Edge TTS voices failed", str(caught.exception))
        self.assertFalse({name for name, _ in self.calls} & FEMALE)

    def test_the_failure_names_every_voice_the_phrase_that_stopped_it_and_the_cause(self):
        # Production build 43702915253: the message named the last voice only, so a refused
        # phrase could not be told apart from an outage.
        failures = {("hi-IN-MadhurNeural", 2): 99, ("en-IN-PrabhatNeural", 0): 99}
        with self.assertRaises(RuntimeError) as caught:
            self.run_edge(failures, sentences=("one.", "two.", "Teesra vakya yahan hai.", "four."))
        message = str(caught.exception)
        self.assertIn('hi-IN-MadhurNeural stopped at phrase 3/4 "Teesra vakya yahan hai."', message)
        self.assertIn('en-IN-PrabhatNeural stopped at phrase 1/4 "one."', message)
        self.assertIn("RuntimeError: NoAudioReceived", message)
        self.assertEqual(len(voice._LAST_EDGE_FAILURES), 2)
        self.assertLess(len(message), 400)

    def test_the_last_try_with_a_voice_sends_the_phrase_without_symbols(self):
        sent = []

        async def fake_synth(text, voice_name, rate, pitch, path):
            sent.append(text)
            if "\u2192" in text:
                raise RuntimeError("NoAudioReceived")
            Path(path).write_bytes(b"mp3")

        def fake_wav(mp3, wav):
            Path(wav).write_bytes(b"RIFF" + b"\x00" * 64)

        with patch.dict("os.environ", {"TTS_VOICE": ""}), \
             patch.object(voice, "_edge_module", return_value=object()), \
             patch.object(voice, "_synth_edge", fake_synth), patch.object(voice, "_to_wav", fake_wav), \
             patch("time.sleep"):
            wavs = voice._synth_all_edge(["Pehla.", "Redshift \u2192 z = 13, yaani 13.4 arab saal!"],
                                         "hinglish", "male", self.build)
        self.assertEqual(len(wavs), 2)
        self.assertEqual(voice._LAST_EDGE_VOICE, "hi-IN-MadhurNeural")
        self.assertEqual(sent[-1], "Redshift z 13, yaani 13.4 arab saal!")
        self.assertEqual(sent.count("Pehla."), 1)

    def test_plain_text_keeps_devanagari_words_whole(self):
        self.assertEqual(voice._plain_speech_text("\u0906\u0915\u093e\u0936\u0917\u0902\u0917\u093e \u2014 13% \u0926\u0942\u0930\u0964"),
                         "\u0906\u0915\u093e\u0936\u0917\u0902\u0917\u093e 13% \u0926\u0942\u0930\u0964")

    def test_a_piece_with_nothing_to_say_is_never_sent_to_the_voice(self):
        chunks = voice._speech_chunks("Pehli baat. ... \u2014 ! Doosri baat?", max_chars=12)
        self.assertEqual(chunks, ["Pehli baat.", "Doosri baat?"])


if __name__ == "__main__":
    unittest.main()
