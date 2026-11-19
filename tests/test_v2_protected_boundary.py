"""The v2 stack must not alter protected workflows or introduce paid publishing."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_DIR = ROOT / "src" / "production_v2"
MANIFEST = json.loads((ROOT / "docs" / "audit" / "protected-workflow-manifest.json").read_text(encoding="utf-8"))


class ProtectedBoundaryTests(unittest.TestCase):
    def test_no_new_workflow_files_or_schedules(self):
        existing = {p.name for p in (ROOT / ".github" / "workflows").glob("*.yml")}
        self.assertEqual(existing, set(MANIFEST["workflows"]))
        self.assertEqual(MANIFEST["schedules"], {
            "daily-news-auto-short.yml": ["45 2 * * *", "45 13 * * *"],
            "daily-news-shorts.yml": ["30 2 * * *", "30 13 * * *"],
            "make_longform.yml": ["0 13 * * 0"],
            "make_shorts.yml": ["30 3 * * *"],
        })

    def test_v2_sources_contain_no_public_publishing_switch(self):
        for path in sorted(V2_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotIn('privacyStatus": "public"', text)
                self.assertNotIn("ALLOW_PUBLIC_PUBLISH=1", text)
                self.assertNotIn('privacyStatus="public"', text)
                self.assertNotIn("schedule:", text)

    def test_existing_uploader_is_only_imported_lazily(self):
        source = (V2_DIR / "publishing.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        top_level_imports = [line for line in lines
                             if line.startswith("from core") or line.startswith("import core")]
        self.assertEqual(top_level_imports, [])
        self.assertIn("from core import uploader", source)

    def test_v2_modules_are_included_in_the_monitored_manifest(self):
        monitored = set(MANIFEST["monitored_files"])
        expected = {"src/__init__.py"} | {
            str(p.relative_to(ROOT)) for p in V2_DIR.glob("*.py")}
        self.assertTrue(expected.issubset(monitored), sorted(expected - monitored))


if __name__ == "__main__":
    unittest.main()
