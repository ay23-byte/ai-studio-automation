"""A picture the final audit would refuse is replaced before the edit, not after the render.

Production build 43681187522 found footage for 89 of 89 scenes, rendered 19 min 26 s, and was
then refused because eight chosen clips were almost black.
"""
import inspect
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core import pipeline
from core.pipeline import repair_unusable_pictures


def _shot(path, repairable=True, seconds=8.0):
    shot = {"_media": {"type": "video", "path": path}, "_dur": seconds, "narration": "mirror segments"}
    if repairable:
        shot["_picture_repair"] = {"keywords": ["webb mirror"], "brief": "webb mirror", "chapter": 0, "beat": 1}
    return shot


class RepairTests(unittest.TestCase):
    def check(self, path, _seconds):
        return "Visual is severely underexposed." if "dark" in str(path) else None

    def test_good_pictures_are_left_alone(self):
        timeline = [_shot("a.mp4"), _shot("b.mp4")]
        calls = []
        records = repair_unusable_pictures(timeline, lambda *args: calls.append(args), check=self.check)
        self.assertEqual(records, [])
        self.assertEqual(calls, [])
        self.assertEqual(timeline[0]["_media"]["path"], "a.mp4")

    def test_a_dark_clip_is_replaced_by_the_next_asset(self):
        timeline = [_shot("a.mp4"), _shot("dark1.mp4")]
        records = repair_unusable_pictures(
            timeline, lambda shot, problem, attempt: {"type": "video", "path": "fine.mp4"}, check=self.check)
        self.assertEqual(timeline[1]["_media"]["path"], "fine.mp4")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["shot"], 2)
        self.assertTrue(records[0]["resolved"])
        self.assertEqual(records[0]["attempts"], 1)
        self.assertEqual(records[0]["original"], "dark1.mp4")

    def test_a_dark_replacement_is_measured_too_and_replaced_again(self):
        answers = iter([{"type": "video", "path": "dark2.mp4"}, None,
                        {"type": "image", "path": "card.jpg", "project_graphic": True}])
        timeline = [_shot("dark1.mp4")]
        records = repair_unusable_pictures(timeline, lambda *args: next(answers), check=self.check)
        self.assertEqual(timeline[0]["_media"]["path"], "card.jpg")
        self.assertTrue(records[0]["resolved"])
        self.assertEqual(records[0]["attempts"], 3)

    def test_nothing_left_to_try_is_reported_as_unresolved(self):
        timeline = [_shot("dark1.mp4")]
        records = repair_unusable_pictures(timeline, lambda *args: None, check=self.check)
        self.assertFalse(records[0]["resolved"])
        self.assertEqual(records[0]["remaining_problem"], "Visual is severely underexposed.")
        self.assertEqual(timeline[0]["_media"]["path"], "dark1.mp4")

    def test_a_failing_replacement_call_does_not_stop_the_repair(self):
        def replace(shot, problem, attempt):
            if attempt == 1:
                raise RuntimeError("provider down")
            return {"type": "video", "path": "fine.mp4"}
        timeline = [_shot("dark1.mp4")]
        records = repair_unusable_pictures(timeline, replace, check=self.check)
        self.assertTrue(records[0]["resolved"])

    def test_presenter_title_and_graphic_shots_are_not_touched(self):
        timeline = [_shot("dark_title.mp4", repairable=False)]
        self.assertEqual(repair_unusable_pictures(timeline, lambda *args: self.fail("called"), check=self.check), [])

    def test_the_default_check_is_the_audit_rule_on_real_files(self):
        with tempfile.TemporaryDirectory() as folder:
            dark, fine, small = Path(folder) / "night.png", Path(folder) / "fine.png", Path(folder) / "small.png"
            Image.new("RGB", (1280, 720), (2, 2, 2)).save(dark)
            Image.effect_noise((1280, 720), 60).convert("RGB").save(fine)
            Image.effect_noise((480, 270), 60).convert("RGB").save(small)
            timeline = [_shot(str(dark)), _shot(str(fine)), _shot(str(small))]
            for shot in timeline:
                shot["_media"]["type"] = "image"
            records = repair_unusable_pictures(
                timeline, lambda shot, problem, attempt: {"type": "image", "path": str(fine)})
        self.assertEqual([item["shot"] for item in records], [1, 3])
        self.assertIn("underexposed", records[0]["problem"])
        self.assertIn("480×270", records[1]["problem"])
        self.assertTrue(all(item["resolved"] for item in records))


class PipelineWiringTests(unittest.TestCase):
    def setUp(self):
        text = Path(inspect.getsourcefile(pipeline)).read_text(encoding="utf-8")
        self.source = text[text.index("def build_longform("):]

    def test_the_repair_runs_before_the_fallback_report_and_the_edit(self):
        repair = self.source.index("repair_unusable_pictures(shot_timeline, replace_picture)")
        self.assertLess(repair, self.source.index("explainer_cards.write_fallback_report("))
        self.assertLess(repair, self.source.index("build_edit_decision_list("))
        self.assertLess(repair, self.source.index("audit_visual_consistency(visual_report, bd)"))

    def test_an_unresolved_picture_stops_the_build_before_the_render_with_a_reason(self):
        self.assertIn("Refusing to render a film that the picture audit would reject.", self.source)
        from core.failure_category import classify_failure
        message = ("No usable footage for 1 shot(s): the picture found is unusable and no replacement exists "
                   "(shot 36: Visual is severely underexposed.). Refusing to render a film that the picture "
                   "audit would reject.")
        self.assertEqual(classify_failure(message)["category"], "asset_shortage")

    def test_a_replacement_card_counts_as_a_fallback_card(self):
        self.assertIn('fallback_state["cards"].append(str(card))', self.source)
        self.assertIn('"project_graphic": True', self.source)


if __name__ == "__main__":
    unittest.main()
