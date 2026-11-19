"""Milestone 5/6/7: purposeful labelled visuals with provenance."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.production_v2.config import V2Config
from src.production_v2.licenses import AssetLibrary
from src.production_v2.models import Scene, VisualSpec
from src.production_v2.visuals import SyntheticVisualGenerator, plan_visuals


def scenes() -> list[Scene]:
    return [
        Scene("a", "First narration", VisualSpec("A dated timeline panel", "diagram",
                                                 on_screen_text="TIMELINE")),
        Scene("b", "Second narration", VisualSpec("A labelled map", "map",
                                                  on_screen_text="MAP")),
    ]


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.config = V2Config.from_env({})

    def test_generator_creates_correct_resolution_and_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self.config.rules_for("news_short")
            path = SyntheticVisualGenerator().draw(scenes()[0], rules, Path(tmp) / "v.png")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1920))
            self.assertTrue(path.stat().st_size > 1000)

    def test_plan_visuals_registers_synthetic_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = AssetLibrary()
            assets = plan_visuals(scenes(), Path(tmp), library, self.config, "news_short")
            self.assertEqual(len(assets), 2)
            self.assertTrue(all(a.record.synthetic for a in assets))
            self.assertTrue(all(a.record.sha256 for a in assets))
            self.assertTrue(all(a.image_path.is_file() for a in assets))
            self.assertTrue(library.all_eligible())

    def test_visual_kinds_are_distinct(self):
        self.assertEqual({s.visual.kind for s in scenes()}, {"diagram", "map"})


if __name__ == "__main__":
    unittest.main()
