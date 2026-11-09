"""Topic-agnostic documentary shot planning helpers.

This module converts script storyboard beats into explicit production directions.
It never fabricates evidence, archival provenance, interviews, or chart data.
"""
from __future__ import annotations

import re

VISUAL_TYPES = {
    "map": ("map", "geographic", "border", "route", "territory", "location map"),
    "chart": ("chart", "graph", "statistics", "data", "percentage", "revenue", "growth", "decline"),
    "timeline": ("timeline", "chronology", "year by year", "decade", "sequence of events"),
    "diagram": ("diagram", "how it works", "mechanism", "equation", "structure", "process"),
    "document": ("document", "letter", "report", "court", "record", "article", "contract", "evidence"),
    "person": ("biography", "leader", "founder", "scientist", "ruler", "person"),
    "location": ("city", "village", "building", "landscape", "region", "place"),
    "archival": ("archival footage", "archive footage", "archival film", "historical footage", "historical film", "historical photograph", "period photograph", "newsreel", "wartime footage", "old footage"),
    "interview": ("interview", "testimony", "expert says", "conversation"),
    "presenter": ("presenter", "host", "on camera", "to camera"),
}

CATEGORY_STYLE = {
    "history": "historically grounded, period-accurate materials and architecture; label reconstructions honestly",
    "geopolitics": "geographically precise locations, borders and political context; neutral visual framing",
    "science": "scientifically accurate apparatus, experiments and observable physical processes",
    "physics": "correct physical models, laboratory apparatus, equations and scale",
    "technology": "specific real devices, interfaces and engineering processes; avoid fictional components",
    "finance": "legible data graphics, real-world economic context and restrained editorial design",
    "mystery": "evidence-led investigative atmosphere; distinguish verified clues from speculation",
    "economics": "accurate economic indicators, institutions, supply chains and data visualisation",
    "general": "subject-specific documentary imagery; avoid generic filler",
}


def infer_visual_type(beat: dict) -> str:
    explicit = str(beat.get("visual_type") or "").strip().lower()
    allowed = {"archival", "stock", "ai_reconstruction", "diagram", "map", "chart",
               "timeline", "document", "person", "location", "interview", "presenter"}
    if explicit in allowed:
        return explicit
    text = " ".join(str(beat.get(key) or "") for key in
                   ("visual_prompt", "prompt", "description", "subject", "keywords"))
    lowered = text.lower()
    for kind, terms in VISUAL_TYPES.items():
        if any(term in lowered for term in terms):
            return kind
    return "stock"


def enrich_visual_beat(beat: dict, category: str = "general") -> dict:
    """Return normalized editorial metadata and a safer, more specific prompt."""
    item = dict(beat)
    kind = infer_visual_type(item)
    subject = str(item.get("subject") or "").strip()
    location = str(item.get("location") or "").strip()
    period = str(item.get("time_period") or "").strip()
    prompt = str(item.get("visual_prompt") or item.get("prompt") or
                 item.get("description") or subject or "subject-specific documentary scene").strip()
    keywords = item.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [str(value).strip() for value in keywords if str(value).strip()]
    search_queries = item.get("search_queries") or []
    if isinstance(search_queries, str):
        search_queries = [search_queries]
    search_queries = [str(value).strip() for value in search_queries if str(value).strip()]
    if not search_queries:
        search_queries = keywords[:]
    archival_requested = kind == "archival" or bool(item.get("archival_required"))
    archival_queries = []
    if archival_requested:
        context_query = " ".join(part for part in (subject, location, period) if part).strip()
        archival_queries = [" ".join(part for part in (context_query, "historical archive footage") if part), " ".join(part for part in (context_query, "period photographs archival film") if part)]
        search_queries = list(dict.fromkeys(search_queries + archival_queries))
    style = CATEGORY_STYLE.get(str(category or "general").lower(), CATEGORY_STYLE["general"])
    guardrails = {
        "archival": "Use only verifiable period material; do not generate or mislabel synthetic imagery as archival.",
        "ai_reconstruction": "Clearly disclose this as an AI reconstruction; never present it as historical source material.",
        "document": "Show only source material supplied or verified by research; never invent readable documents, headlines or quotations.",
        "interview": "Use a genuine recorded interview only; never fabricate a real person's testimony or lip-sync.",
        "chart": "Use only sourced numeric values; if values are unavailable, use a qualitative graphic without invented numbers.",
        "map": "Keep geography and borders accurate for the stated time period; avoid unsupported boundary claims.",
        "diagram": "Keep labels and mechanisms technically correct; prefer a clean explanatory graphic.",
        "timeline": "Use only dates/events established in the script's verified research.",
    }
    if archival_requested:
        guardrails["archival"] = ("Prefer genuine, dated archival film or period photographs from an identifiable archive. " "Record the item page, creator/holding institution, date or date range, rights statement and required credit. " "Do not label a modern reenactment or AI image as archival.")
    context = "; ".join(part for part in (subject, location, period) if part)
    item.update({
        "visual_type": kind,
        "subject": subject,
        "location": location,
        "time_period": period,
        "search_queries": search_queries[:5],
        "archival_required": archival_requested,
        "archival_search_queries": archival_queries,
        "keywords": keywords[:5] or ([subject] if subject else ["documentary subject"]),
        "shot_size": str(item.get("shot_size") or "medium or wide establishing shot"),
        "camera_movement": str(item.get("camera_movement") or "subtle controlled push-in or lateral movement"),
        "editorial_style": style,
        "visual_guardrail": guardrails.get(kind, "Show the exact subject and action described by the narration; do not use unrelated stock footage."),
        "production_prompt": (
            f"{style}. {prompt}. Specific context: {context or 'as supported by narration'}. "
            f"{guardrails.get(kind, 'Match the narration precisely; no unrelated filler.')} "
            "16:9 premium documentary composition, strong focal subject, natural cinematic lighting, "
            "no watermarks, no logos, no fabricated text."
        ),
    })
    return item


def plan_section_visuals(section: dict, category: str = "general") -> list[dict]:
    raw = section.get("visuals") or [{"visual_prompt": section.get("image") or section.get("title") or "documentary establishing shot",
                                    "keywords": section.get("keywords") or []}]
    planned = []
    for item in raw:
        if not item:
            continue
        structured = isinstance(item, dict)
        beat = enrich_visual_beat(item if structured else {"visual_prompt": str(item)}, category)
        beat["_structured_input"] = structured
        planned.append(beat)
    return planned
