"""Phase 3 content intelligence: topic planning, script review and Shorts repurposing.

The model can suggest research directions, but factual claims are only marked as
source-supported when the user supplies supporting source material.
"""
import json
from pathlib import Path

from core.script_engine import _ask_gemini, _parse


def _ask_json(prompt: str) -> dict:
    data = _parse(_ask_gemini(prompt))
    if not isinstance(data, dict):
        raise ValueError("AI response was not a JSON object.")
    return data


def research_topics(niche: str, audience: str = "Hindi/Hinglish beginners",
                    category: str = "education", count: int = 10) -> dict:
    """Create topic ideas and search plans; do not fabricate current trends or sources."""
    count = max(3, min(int(count), 20))
    prompt = f"""Act as a careful YouTube editorial researcher.
Niche: {niche}
Target audience: {audience}
Category: {category}
Return {count} distinct video ideas as JSON only.
Schema:
{{"niche":"...","audience":"...","ideas":[{{"topic":"...","format":"shorts or longform","viewer_question":"...","hook":"...","angle":"...","key_points":["..."],"research_queries":["specific search query 1","specific search query 2"],"claims_to_verify":["factual claim that needs a reliable source"],"evergreen_or_time_sensitive":"evergreen or time-sensitive"}}]}}
Rules:
- Do not claim these are trending, popular, searched, or current; no live analytics/search data is available.
- Do not invent citations, URLs, statistics, dates, studies, or expert quotations.
- Prefer useful, specific ideas with a clear viewer question and distinct angle.
- For finance/health/legal topics, keep educational framing and flag high-stakes claims for expert sources.
"""
    result = _ask_json(prompt)
    result["ideas"] = result.get("ideas", [])[:count]
    return result


def review_script(script: dict, source_material: str = "") -> dict:
    """Critique and improve a script while distinguishing evidence from unsupported claims."""
    prompt = f"""Review this YouTube script as a rigorous Hindi/Hinglish editor and fact-checking assistant.
SCRIPT JSON:
{json.dumps(script, ensure_ascii=False)}
USER-SUPPLIED SOURCE MATERIAL (may be empty):
{source_material[:16000] or "No source material supplied."}

Return JSON only with this schema:
{{"overall_score":0,"strengths":["..."],"issues":[{{"severity":"high/medium/low","area":"hook/accuracy/clarity/structure/retention/language","problem":"...","fix":"..."}}],"claim_audit":[{{"claim":"...","status":"supported_by_supplied_material / needs_verification / contradicted_by_supplied_material","evidence":"brief supporting quote or explanation from supplied material, or empty","source_reference":"exact URL/title supplied by user, or empty"}}],"improved_script":{{}},"editor_notes":["..."]}}
Rules:
- Never invent facts, sources, citations, URLs, dates, or statistics.
- Mark claims needs_verification if the supplied material does not support them.
- Use contradicted_by_supplied_material only when the supplied text clearly conflicts with the claim.
- Preserve the script's format and core topic; improve hook, pacing, clarity, natural spoken Hindi and CTA.
- Keep financial content educational, without investment promises or personalized advice.
- If source material is empty, say explicitly that factual verification is incomplete.
"""
    result = _ask_json(prompt)
    result.setdefault("claim_audit", [])
    result.setdefault("issues", [])
    result.setdefault("editor_notes", [])
    result.setdefault("improved_script", script)
    if not source_material.strip():
        result["verification_notice"] = (
            "No source material was supplied. Claims have not been externally verified; "
            "items requiring evidence should remain marked needs_verification."
        )
    return result


def convert_longform_to_shorts(script: dict, count: int = 3) -> dict:
    """Repurpose a long-form script into standalone Shorts scripts."""
    count = max(1, min(int(count), 8))
    prompt = f"""Convert this long-form YouTube script into {count} distinct standalone Hindi/Hinglish Shorts.
LONG-FORM SCRIPT:
{json.dumps(script, ensure_ascii=False)[:24000]}

Return JSON only:
{{"source_title":"...","shorts":[{{"title":"Roman Hinglish title max 80 chars","hook":"Roman Hinglish hook","category":"...","format":"shorts","scenes":[{{"say":"Devanagari spoken Hindi, no English letters or digits","caption":"Roman Hinglish equivalent","label":"1-3 Roman words","image":"English visual prompt, no text/logos/faces","keywords":["stock footage keyword"]}}]}}]}}
Rules:
- Each Short must make sense independently, with its own hook, explanation and ending CTA.
- 4-6 scenes per Short; 10-22 spoken words per scene; concise, natural narration.
- Do not simply cut sentences mid-thought. Avoid repeating the same angle across Shorts.
- Do not add facts, numbers, claims, or examples that are absent from the source script.
- Keep disclaimers for finance/health/legal subjects; no personalized advice or promises.
- Use Devanagari for spoken Hindi, spell numbers in words, no markdown.
"""
    result = _ask_json(prompt)
    shorts = result.get("shorts", [])
    for item in shorts:
        item["format"] = "shorts"
        item.setdefault("category", script.get("category", "education"))
    result["shorts"] = shorts[:count]
    return result


def save_content_artifact(data: dict, name: str) -> Path:
    """Save a generated content-lab result under output/content_lab."""
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in name).strip("_")[:60] or "result"
    folder = Path("output") / "content_lab"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
