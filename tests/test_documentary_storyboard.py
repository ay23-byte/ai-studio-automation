from core.documentary_storyboard import enrich_visual_beat, plan_section_visuals


def test_chart_guardrail_prevents_invented_values():
    beat = enrich_visual_beat({
        "visual_type": "chart",
        "visual_prompt": "Revenue growth chart",
        "narration_anchor": "revenue increased",
        "keywords": ["company revenue"],
    }, "finance")
    assert "sourced numeric values" in beat["visual_guardrail"]
    assert beat["visual_type"] == "chart"
    assert "16:9" in beat["production_prompt"]


def test_interview_is_not_fabricated():
    beat = enrich_visual_beat({"visual_prompt": "Expert interview close-up"})
    assert beat["visual_type"] == "interview"
    assert "genuine recorded interview only" in beat["visual_guardrail"]


def test_ai_reconstruction_is_disclosed():
    beat = enrich_visual_beat({"visual_type": "ai_reconstruction", "visual_prompt": "Ancient city"})
    assert "AI reconstruction" in beat["visual_guardrail"]


def test_archival_beat_adds_archive_queries_and_guardrail():
    beat = enrich_visual_beat({"visual_type": "archival", "subject": "Kolkata", "time_period": "1940s"})
    assert beat["archival_required"] is True
    assert any("historical archive footage" in query for query in beat["archival_search_queries"])
    assert "genuine, dated archival" in beat["visual_guardrail"]
    assert "AI image as archival" in beat["visual_guardrail"]


def test_newsreel_is_inferred_as_archival():
    beat = enrich_visual_beat({"visual_prompt": "1940s newsreel of wartime factories"})
    assert beat["visual_type"] == "archival"
    assert beat["archival_required"] is True


def test_plan_preserves_specific_search_terms_and_context():
    section = {"title": "A chapter", "visuals": [{
        "visual_prompt": "Close-up of a specific telescope",
        "narration_anchor": "the telescope captured the signal",
        "subject": "radio telescope",
        "location": "Pune",
        "time_period": "2020s",
        "keywords": ["radio telescope Pune"],
    }]}
    planned = plan_section_visuals(section, "science")
    assert planned[0]["subject"] == "radio telescope"
    assert planned[0]["location"] == "Pune"
    assert planned[0]["search_queries"] == ["radio telescope Pune"]
    assert planned[0]["visual_type"] == "stock"
