import csv
import json
from pathlib import Path

from core.asset_licensing import build_licence_register, licence_is_usable, score_asset


def test_unknown_licence_is_blocked():
    ok, reason = licence_is_usable({"provider": "Internet Archive", "licence_status": "check rights"})
    assert ok is False
    assert reason == "unclear_or_unrecognized_licence"


def test_known_stock_licence_is_only_pending_human_review():
    ok, _ = licence_is_usable({
        "provider": "Pixabay",
        "licence": "Pixabay Content License",
        "licence_url": "https://pixabay.com/service/license-summary/",
    })
    assert ok is True


def test_register_writes_csv_and_credits_and_blocks_missing_provenance(tmp_path):
    clip = tmp_path / "sample.mp4"
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
        {"_start": 100, "_media": {"path": str(tmp_path / "missing.mp4"), "type": "video"}},
    ], tmp_path / "build")
    assert Path(result["csv_path"]).is_file()
    assert Path(result["credits_path"]).is_file()
    with open(result["csv_path"], newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["use_status"] == "eligible_pending_human_review"
    assert rows[1]["use_status"] == "blocked"
    assert result["blocked_count"] == 1


def test_score_does_not_claim_unobserved_visual_checks():
    score = score_asset({"relevance_score": 0.9, "licence": "CC BY 4.0",
                         "licence_url": "https://creativecommons.org/licenses/by/4.0/"}, 1920, 1080)
    assert score["score_0_10"] == 8.5
    assert score["steady_clean_points"] is None
    assert score["watermark_logo_points"] is None
    assert score["privacy_points"] is None
