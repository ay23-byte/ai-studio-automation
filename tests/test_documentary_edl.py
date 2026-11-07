from pathlib import Path

from core.documentary_edl import build_edit_decision_list, _pacing_profile


def test_tone_selects_expected_pacing_profile():
    assert _pacing_profile("investigative")[0] == "medium"
    assert _pacing_profile("technology explainer")[0] == "fast"
    assert _pacing_profile("nature biography")[0] == "slow"


def test_edl_uses_narration_timeline_and_emits_chapter_music():
    sections = [
        {"title": "Origins", "narration": "The village changed over time.",
         "_start": 0.0, "_dur": 8.0},
        {"title": "Consequences", "narration": "Families moved for work.",
         "_start": 8.0, "_dur": 8.0},
    ]
    shots = [
        {"_chapter_index": 0, "_start": 0.0, "_dur": 8.0,
         "narration": "The village changed over time.",
         "visual_prompt": "Aerial view of a rural village",
         "visual_type": "stock", "_media": {"type": "video", "path": __file__}},
        {"_chapter_index": 1, "_start": 8.0, "_dur": 8.0,
         "narration": "Families moved for work.",
         "visual_prompt": "Workers leaving a rural town",
         "visual_type": "stock", "_media": {"type": "video", "path": __file__}},
    ]
    edl = build_edit_decision_list(shots, sections, 16.0, "investigative")
    assert edl["pacing_profile"] == "medium"
    assert len(edl["timeline"]) == 2
    assert len(edl["music"]) == 2
    assert edl["timeline"][1]["transition_in"] == "dip_black"
    assert edl["timeline"][0]["visual"]["asset_id"] == Path(__file__).name
    assert edl["qa_report"]["human_editorial_review_required"] is True


def test_missing_assets_and_long_overlays_are_flagged():
    sections = [{"narration": "A number was revealed. The case changed.",
                 "_start": 0.0, "_dur": 12.0}]
    shots = [{
        "_chapter_index": 0, "_start": 0.0, "_dur": 12.0,
        "narration": "A number was revealed.",
        "visual_prompt": "Evidence board with verified statistic",
        "visual_type": "evidence",
        "on_screen_text": "This is an excessively long overlay that must be excluded",
        "_media": {"type": "video", "path": "/definitely/missing/asset.mp4"},
    }]
    edl = build_edit_decision_list(shots, sections, 12.0, "investigative")
    assert edl["missing_asset_requests"]
    assert edl["timeline"][0]["overlay"] == []
    assert edl["qa_report"]["unmatched_narration_sentences"]
    assert any("omitted overlay" in item for item in edl["qa_report"]["fixes_applied"])
