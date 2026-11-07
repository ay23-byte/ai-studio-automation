import tempfile
import unittest
from pathlib import Path

from scripts.prepare_news_short_assets import (
    prepare_candidate_script,
    validate_draft,
    find_one,
    visual_plan,
    create_story_graphic,
)


class PrepareNewsShortAssetsTests(unittest.TestCase):
    def setUp(self):
        self.packet = {
            "review_status": "approved",
            "sources": [{"source_id": "S2", "title": "Primary study"}],
            "verified_facts": [{"fact": "Study estimate", "source_ids": ["S2"]}],
        }
        self.script = {
            "title": "Himalayan flood exposure study",
            "editorial_status": "draft_requires_human_script_and_visual_review",
            "scenes": [
                {"say": f"नरेशन {i}", "caption": f"Caption {i}", "image": f"Visual {i}",
                 "keywords": ["Himalaya river"], "source_ids": ["S2"]}
                for i in range(4)
            ],
        }

    def test_accepts_reviewed_packet_and_script(self):
        validate_draft(self.packet, self.script)

    def test_rejects_unapproved_packet(self):
        with self.assertRaisesRegex(ValueError, "must be approved"):
            validate_draft(dict(self.packet, review_status="pending"), self.script)

    def test_rejects_non_draft_script(self):
        with self.assertRaisesRegex(ValueError, "human-review script draft"):
            validate_draft(self.packet, dict(self.script, editorial_status="approved_for_production"))

    def test_rejects_unknown_source_reference(self):
        script = dict(self.script)
        script["scenes"] = [dict(scene) for scene in self.script["scenes"]]
        script["scenes"][0]["source_ids"] = ["S99"]
        with self.assertRaisesRegex(ValueError, "known source IDs"):
            validate_draft(self.packet, script)

    def test_rejects_missing_visual_prompt(self):
        script = dict(self.script)
        script["scenes"] = [dict(scene) for scene in self.script["scenes"]]
        script["scenes"][0]["image"] = ""
        with self.assertRaisesRegex(ValueError, "missing image"):
            validate_draft(self.packet, script)

    def test_candidate_does_not_self_approve(self):
        assets = [
            {"asset_type": "image", "asset_path": f"/tmp/scene{i}.jpg",
             "ai_disclosure": True, "asset_review_status": "pending_human_visual_and_rights_review"}
            for i in range(4)
        ]
        candidate = prepare_candidate_script(self.script, self.packet, assets)
        self.assertEqual(candidate["editorial_status"], "draft_assets_require_human_visual_and_rights_review")
        self.assertEqual(candidate["visual_review_status"], "pending_human_visual_and_rights_review")
        self.assertEqual(len(candidate["sources"]), 1)

    def test_himalayan_story_uses_curated_visuals_instead_of_generic_research_search(self):
        self.assertEqual(visual_plan(1, 5, True)["queries"][0], "Himalaya mountain")
        self.assertEqual(visual_plan(2, 5, True)["graphic"], "building_growth")
        self.assertEqual(visual_plan(3, 5, True)["graphic"], "nepal_breakdown")
        self.assertEqual(visual_plan(4, 5, True)["graphic"], "not_forecast")
        self.assertEqual(visual_plan(5, 5, True)["graphic"], "outro")
        self.assertEqual(visual_plan(5, 5, False), {})

    def test_story_graphics_are_local_and_have_provenance_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metric.jpg"
            metadata = create_story_graphic(path, "building_growth")
            self.assertTrue(path.is_file())
            self.assertEqual(metadata["asset_type"], "programmatic_graphic")
            self.assertFalse(metadata["ai_generated"])
            nepal = Path(temp) / "nepal.jpg"
            create_story_graphic(nepal, "nepal_breakdown")
            self.assertTrue(nepal.is_file())
            disclaimer = Path(temp) / "disclaimer.jpg"
            create_story_graphic(disclaimer, "not_forecast")
            self.assertTrue(disclaimer.is_file())
            sidecar = path.with_suffix(".jpg.source.json")
            self.assertTrue(sidecar.is_file())

    def test_find_one_requires_exactly_one_match(self):
        with tempfile.TemporaryDirectory() as temp:
            nested = Path(temp) / "nested"
            nested.mkdir()
            (nested / "packet.json").write_text("{}", encoding="utf-8")
            self.assertEqual(find_one(temp, "packet.json"), nested / "packet.json")
            with self.assertRaisesRegex(ValueError, "found 0"):
                find_one(temp, "missing.json")


if __name__ == "__main__":
    unittest.main()
