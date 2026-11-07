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



def test_video_broll_gets_subtle_time_based_camera_drift():
    from core.editor_longform import _video_motion_expressions

    x_expr, y_expr = _video_motion_expressions(3)
    assert "sin(t*0.12" in x_expr
    assert "cos(t*0.10" in y_expr
    assert "0.18" in x_expr
    assert "0.14" in y_expr


def test_longform_visual_planner_keeps_up_to_ten_distinct_beats():
    from core.pipeline import _visual_beats_for_section

    section = {
        "narration": " ".join(f"narration beat {i}" for i in range(11)),
        "visuals": [
            {
                "narration_anchor": f"narration beat {i}",
                "visual_prompt": f"Distinct documentary shot {i}",
                "keywords": [f"subject {i}"],
                "visual_type": "stock",
            }
            for i in range(11)
        ],
    }
    assert len(_visual_beats_for_section(section)) == 10
