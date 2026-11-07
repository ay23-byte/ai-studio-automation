from core.editor_longform import _pacing_duration, _transition_for


def test_graphic_beats_get_calm_transitions():
    previous = {"visual_type": "stock", "_chapter_index": 0}
    current = {"visual_type": "chart", "_chapter_index": 0}
    assert _transition_for(previous, current, 1) == "fade"


def test_chapter_boundary_gets_editorial_wipe():
    previous = {"visual_type": "stock", "_chapter_index": 0}
    current = {"visual_type": "presenter", "_chapter_index": 1}
    assert _transition_for(previous, current, 1) == "wipeleft"


def test_visual_pacing_bounds_depend_on_scene_type():
    assert _pacing_duration({"visual_type": "chart", "_dur": 20}) == 10
    assert _pacing_duration({"visual_type": "stock", "_dur": 1}) == 3.5
