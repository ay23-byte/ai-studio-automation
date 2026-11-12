"""Edge TTS is an optional provider: selecting a voice must not require it.

Regression for scheduled run 43305710825 ("Daily news Shorts - research queue"):
``core/voice.py`` imported ``edge_tts`` at module level, so a workflow that
installs only ``requests`` and ``Pillow`` crashed while *collecting* a test that
only calls ``pick_voice``.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import voice

ROOT = Path(__file__).resolve().parents[1]

# Makes ``import edge_tts`` raise ImportError in a child interpreter even when
# the package is installed, reproducing the lightweight workflow exactly.
BLOCK_EDGE = "import sys; sys.modules['edge_tts'] = None\n"


def _run_without_edge(code: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env.update({
            "AI_STUDIO_DB_PATH": str(Path(tmp) / "studio.sqlite"),
            "AI_STUDIO_OUTPUT_DIR": str(Path(tmp) / "output"),
            "AI_STUDIO_BUILD_DIR": str(Path(tmp) / "build"),
            "AI_STUDIO_STATE_DIR": str(Path(tmp) / "state"),
            "PYTHONPATH": str(ROOT),
        })
        return subprocess.run([sys.executable, "-c", BLOCK_EDGE + code], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120)


class ImportWithoutEdgeTests(unittest.TestCase):
    def test_voice_module_imports_and_selects_voices_without_edge_tts(self):
        result = _run_without_edge(
            "from core.voice import pick_voice, _edge_voice_candidates, provider_status\n"
            "import json\n"
            "print(json.dumps({'voice': pick_voice('english', 'female'),\n"
            "                  'candidates': _edge_voice_candidates('hinglish', 'female'),\n"
            "                  'edge': provider_status()['edge']}))\n")
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["voice"], "en-IN-NeerjaNeural")
        self.assertIn("hi-IN-SwaraNeural", data["candidates"])
        self.assertFalse(data["edge"])

    def test_synthesis_without_edge_tts_reports_the_missing_provider_clearly(self):
        result = _run_without_edge(
            "from core import voice\n"
            "try:\n"
            "    voice._edge_module()\n"
            "except voice.VoiceProviderUnavailable as exc:\n"
            "    print('UNAVAILABLE:', exc)\n")
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertIn("UNAVAILABLE: Edge TTS is not installed", result.stdout)
        self.assertIn("pip install edge-tts", result.stdout)

    def test_every_news_test_module_imports_with_only_the_collect_workflow_packages(self):
        """The collect workflow installs requests + Pillow and runs test_*news*.py."""
        blocked = ("edge_tts", "googleapiclient", "google", "google_auth_oauthlib", "dotenv",
                   "pandas", "numpy", "streamlit", "yaml", "pytest")
        code = (
            "import sys, importlib, pathlib\n"
            f"for name in {blocked!r}: sys.modules[name] = None\n"
            "sys.path.insert(0, 'tests')\n"
            "failed = []\n"
            "for path in sorted(pathlib.Path('tests').glob('test_*news*.py')):\n"
            "    try:\n"
            "        importlib.import_module(path.stem)\n"
            "    except Exception as exc:\n"
            "        failed.append(f'{path.name}: {type(exc).__name__}: {exc}')\n"
            "print('FAILED=' + repr(failed))\n")
        result = _run_without_edge(code)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        self.assertIn("FAILED=[]", result.stdout, result.stdout[-1500:])


class ProviderCascadeTests(unittest.TestCase):
    def test_missing_edge_package_is_not_retried_per_voice(self):
        with patch.object(voice, "edge_tts", None), \
             patch.object(voice, "_edge_voice_candidates") as candidates:
            with self.assertRaises(voice.VoiceProviderUnavailable):
                voice._synth_all_edge(["hello"], "english", "female", Path("."))
        candidates.assert_not_called()

    def test_missing_edge_package_is_not_retried_with_sleeps(self):
        async def run():
            with patch.object(voice, "edge_tts", None), patch.object(voice.asyncio, "sleep") as sleep:
                with self.assertRaises(voice.VoiceProviderUnavailable):
                    await voice._synth_edge("hello", "en-IN-NeerjaNeural", "+0%", "+0Hz", Path("x.mp3"))
                sleep.assert_not_called()
        asyncio.run(run())

    def test_narration_falls_back_to_offline_voice_and_records_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)

            def fake_espeak(sentences, language, folder):
                wav = build_dir / "s0.wav"
                wav.write_bytes(b"RIFF" + b"\x00" * 128)
                return [wav]

            def fake_join(wavs, pauses, output, speed=1.0):
                output.write_bytes(b"RIFF" + b"\x00" * 128)

            with patch.dict("os.environ", {"TTS_ENGINE": "edge"}), \
                 patch.object(voice, "edge_tts", None), \
                 patch.object(voice, "_synth_all_espeak", side_effect=fake_espeak) as offline, \
                 patch.object(voice, "_dur", return_value=1.0), \
                 patch.object(voice, "_join", side_effect=fake_join):
                output, _ = voice._synthesise_raw("Physics explains the world.", build_dir, language="english")
            offline.assert_called_once()
            report = json.loads((build_dir / "narration_synthesis_report.json").read_text(encoding="utf-8"))
        self.assertTrue(output.name.endswith("voice.wav"))
        self.assertEqual(report["requested_engine"], "edge")
        self.assertEqual(report["actual_engine"], "espeak-ng")
        self.assertIn("Edge TTS is not installed", report["fallback_reason"])
        self.assertFalse(report["providers_available"]["edge"])

    def test_no_provider_at_all_fails_with_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(voice, "edge_tts", None), \
             patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(voice.VoiceProviderUnavailable, "No narration provider is available"):
                voice._synthesise_raw("Physics explains the world.", Path(tmp), language="english")
            self.assertFalse((Path(tmp) / "voice.wav").exists())
            self.assertFalse((Path(tmp) / "narration_synthesis_report.json").exists())

    def test_report_names_the_voice_that_actually_spoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)

            def fake_edge(sentences, language, gender, folder):
                voice._LAST_EDGE_VOICE = "en-US-AriaNeural"      # a fallback voice answered
                wav = build_dir / "s0.wav"
                wav.write_bytes(b"RIFF" + b"\x00" * 128)
                return [wav]

            def fake_join(wavs, pauses, output, speed=1.0):
                output.write_bytes(b"RIFF" + b"\x00" * 128)

            with patch.dict("os.environ", {"TTS_ENGINE": "edge"}), \
                 patch.object(voice, "_synth_all_edge", side_effect=fake_edge), \
                 patch.object(voice, "_dur", return_value=1.0), \
                 patch.object(voice, "_join", side_effect=fake_join):
                voice._synthesise_raw("Physics explains the world.", build_dir, language="english", gender="female")
            report = json.loads((build_dir / "narration_synthesis_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["voice"], "en-US-AriaNeural")
        self.assertEqual(report["configured_voice"], voice.pick_voice("english", "female"))

    def test_provider_status_is_a_plain_report(self):
        status = voice.provider_status()
        self.assertEqual(set(status), {"edge", "chatterbox", "espeak-ng"})
        self.assertTrue(all(isinstance(value, bool) for value in status.values()))


class WorkflowDependencyTests(unittest.TestCase):
    def test_workflows_that_synthesise_speech_install_edge_tts(self):
        """A workflow that renders narration must install the provider it requests."""
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is a dev dependency")
        requirement_files = {
            name: (ROOT / name).read_text(encoding="utf-8").lower()
            for name in ("requirements.txt", "requirements-ci.txt")
        }
        self.assertIn("edge-tts", requirement_files["requirements.txt"])
        self.assertIn("edge-tts", requirement_files["requirements-ci.txt"])
        render_markers = ("run.py shorts", "run.py long", "run_daily.py", "run_daily_news_short",
                          "render_reviewed_news_short", "render_standard_short_review",
                          "render_longform_script", "create_voice_audition")
        missing = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            yaml.safe_load(text)
            if not any(marker in text for marker in render_markers):
                continue
            installs = text.lower()
            if "edge-tts" in installs or any(f"-r {name}" in installs for name in requirement_files):
                continue
            missing.append(path.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
