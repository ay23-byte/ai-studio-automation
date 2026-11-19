"""Milestone 3/4/5/6/7: per-content-type script structure and validation."""
from __future__ import annotations

import unittest

from src.production_v2.config import V2Config
from src.production_v2.models import Claim, ResearchDossier, Source
from src.production_v2.script_engine import build_script, validate_script


def dossier(content_type: str) -> ResearchDossier:
    sources = [Source("s1", "Record", "Official", "https://example.org/a", "2026-01-01", "primary")]
    claims = [Claim("c1", "A measured value was published by the source.", "established",
                    ["s1"], "excerpt"),
              Claim("c2", "The record also states when the value was published.",
                    "established", ["s1"], "excerpt")]
    return ResearchDossier(
        topic="A demonstration topic", content_type=content_type,
        thesis="The claim is supported by a record.", sources=sources,
        claims=claims, demo_mode=True)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.config = V2Config.from_env({})

    def test_all_three_script_types_have_distinct_structure(self):
        scripts = {kind: build_script(dossier(kind), self.config) for kind in
                   ("documentary", "news_short", "physics_short")}
        self.assertIn("record", scripts["documentary"].scenes[1].narration.lower())
        self.assertTrue(any("according to" in s.narration.lower()
                            for s in scripts["news_short"].scenes))
        self.assertTrue(any(s.visual.kind == "animation" for s in scripts["physics_short"].scenes))
        for script in scripts.values():
            assessment = validate_script(script, dossier(script.content_type),
                                         self.config.rules_for(script.content_type))
            self.assertTrue(assessment.accepted, assessment.issues)

    def test_every_narration_scene_has_a_visual(self):
        script = build_script(dossier("documentary"), self.config)
        for scene in script.scenes:
            self.assertTrue(scene.narration.strip())
            self.assertTrue(scene.visual.description.strip())
            self.assertTrue(scene.visual.claim_ids or scene.id.startswith("scene_ho")
                            or scene.id.startswith("scene_ta"))

    def test_news_without_attribution_is_rejected(self):
        data = dossier("news_short")
        data.sources[0].publisher = ""
        script = build_script(data, self.config)
        for scene in script.scenes:
            scene.narration = "This demonstration panel has a neutral label."
        assessment = validate_script(script, data, self.config.rules_for("news_short"))
        self.assertFalse(assessment.accepted)

    def test_devanagari_is_rejected_in_english_script(self):
        script = build_script(dossier("physics_short"), self.config)
        script.scenes[1].narration = "यह एक परीक्षण है"
        assessment = validate_script(script, dossier("physics_short"),
                                     self.config.rules_for("physics_short"))
        self.assertFalse(assessment.accepted)
        self.assertTrue(any("Devanagari" in issue for issue in assessment.issues))

    def test_allegation_cannot_be_narrated_as_fact(self):
        data = dossier("news_short")
        data.claims[0].status = "allegation"
        script = build_script(data, self.config)
        for scene in script.scenes:
            if "c1" in scene.visual.claim_ids:
                scene.narration = scene.narration.replace("According to Official, ", "")
                scene.narration = "A measured value was published by the source."
        assessment = validate_script(script, data, self.config.rules_for("news_short"))
        self.assertFalse(assessment.accepted)


if __name__ == "__main__":
    unittest.main()
