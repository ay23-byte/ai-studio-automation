"""Regression tests for distinguishing historical context from real archival media."""
from core.documentary_storyboard import enrich_visual_beat, infer_visual_type
from core.footage_sources import _is_archival_video_request


def test_generic_historical_context_is_not_forced_into_archive_only_mode():
    beat = {
        "visual_prompt": "Historically grounded reconstruction of the Chernobyl control room",
        "subject": "Chernobyl control room",
        "time_period": "1986",
    }
    assert infer_visual_type(beat) != "archival"
    planned = enrich_visual_beat(beat, "history")
    assert planned["archival_required"] is False
    assert _is_archival_video_request(planned["keywords"], planned["production_prompt"]) is False


def test_ai_reconstruction_guardrail_does_not_trigger_archive_only_mode():
    planned = enrich_visual_beat({
        "visual_type": "ai_reconstruction",
        "visual_prompt": "A historically accurate control room reconstruction",
        "subject": "Chernobyl control room",
        "time_period": "1986",
    }, "history")
    assert "AI reconstruction" in planned["visual_guardrail"]
    assert "archival footage" not in planned["visual_guardrail"]
    assert _is_archival_video_request(planned["keywords"], planned["production_prompt"]) is False


def test_explicit_newsreel_still_requires_real_archival_video():
    assert _is_archival_video_request(
        ["Chernobyl 1986"],
        "Genuine Soviet newsreel showing the 1986 evacuation",
    ) is True


def test_explicit_archival_photograph_is_still_classified_as_archival():
    assert infer_visual_type({
        "visual_prompt": "A dated archival photograph of Pripyat in 1986",
    }) == "archival"
