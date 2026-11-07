import csv
import json
import tempfile
import unittest
from pathlib import Path

from core.asset_licensing import build_licence_register, licence_is_usable, score_asset


class AssetLicensingTests(unittest.TestCase):
    def test_unknown_licence_is_blocked(self):
        ok, reason = licence_is_usable({"provider": "Internet Archive", "licence_status": "check rights"})
        self.assertFalse(ok)
        self.assertEqual(reason, "unclear_or_unrecognized_licence")

    def test_known_stock_licence_is_only_pending_human_review(self):
        ok, _ = licence_is_usable({
            "provider": "Pixabay",
            "licence": "Pixabay Content License",
            "licence_url": "https://pixabay.com/service/license-summary/",
        })
        self.assertTrue(ok)

    def test_register_writes_csv_and_credits_and_blocks_missing_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / "sample.mp4"
            clip.write_bytes(b"placeholder media")
            sidecar = clip.with_suffix(".mp4.source.json")
            sidecar.write_text(json.dumps({
                "provider": "Pixabay", "asset_id": 123, "title": "Rural village",
                "creator": "Example creator", "page_url": "https://pixabay.com/videos/example/",
                "licence": "Pixabay Content License",
                "licence_url": "https://pixabay.com/service/license-summary/",
                "attribution_text": "Rural village by Example creator — Pixabay",
                "date_accessed": "2026-11-07", "relevance_score": 0.9,
                "width": 1920, "height": 1080,
            }), encoding="utf-8")
            result = build_licence_register([
                {"_start": 0, "_media": {"path": str(clip), "type": "video"}},
                {"_start": 100, "_media": {"path": str(root / "missing.mp4"), "type": "video"}},
            ], root / "build")
            self.assertTrue(Path(result["csv_path"]).is_file())
            self.assertTrue(Path(result["credits_path"]).is_file())
            with open(result["csv_path"], newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["use_status"], "eligible_pending_human_review")
            self.assertEqual(rows[1]["use_status"], "blocked")
            self.assertEqual(result["blocked_count"], 1)

    def test_project_created_graphic_is_eligible_without_external_stock_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graphic = root / "metric.jpg"
            graphic.write_bytes(b"placeholder image")
            graphic.with_suffix(".jpg.source.json").write_text(json.dumps({
                "provider": "LabLens/AI Studio local graphic renderer",
                "asset_type": "programmatic_graphic",
                "ai_generated": False,
                "font_licence": "Bitstream Vera and DejaVu font licence",
                "font_licence_url": "https://dejavu-fonts.github.io/License.html",
                "attribution_text": "Original graphic created by project; DejaVu font notice.",
                "licence_status": "Created by this project",
                "graphic_kind": "building_growth",
            }), encoding="utf-8")
            result = build_licence_register([
                {"_start": 0, "_media": {"path": str(graphic), "type": "image"}}
            ], root / "build")
            with open(result["csv_path"], newline="", encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["use_status"], "eligible_pending_human_review")
            self.assertFalse(result["blocked_assets"])
            self.assertIn("Original graphic created", Path(result["credits_path"]).read_text(encoding="utf-8"))

    def test_score_does_not_claim_unobserved_visual_checks(self):
        score = score_asset({"relevance_score": 0.9, "licence": "CC BY 4.0",
                             "licence_url": "https://creativecommons.org/licenses/by/4.0/"}, 1920, 1080)
        self.assertEqual(score["score_0_10"], 8.5)
        self.assertIsNone(score["steady_clean_points"])
        self.assertIsNone(score["watermark_logo_points"])
        self.assertIsNone(score["privacy_points"])


if __name__ == "__main__":
    unittest.main()
