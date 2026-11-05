"""Import-time smoke tests for production modules used by the app."""
import importlib
import unittest


class RuntimeImportTests(unittest.TestCase):
    def test_core_modules_import_cleanly(self):
        modules = (
            "core.config", "core.db", "core.monitoring", "core.images",
            "core.voice", "core.script_engine", "core.pipeline",
            "core.quality", "core.uploader", "core.analytics", "core.seo",
            "core.scheduler", "core.performance",
        )
        for name in modules:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_instrumented_entry_points_are_callable(self):
        from core.pipeline import build_shorts, build_longform
        from core.images import generate_image, fetch_clip
        from core.voice import synthesise
        from core.script_engine import _ask_gemini
        for function in (build_shorts, build_longform, generate_image,
                         fetch_clip, synthesise, _ask_gemini):
            self.assertTrue(callable(function))


if __name__ == "__main__":
    unittest.main()