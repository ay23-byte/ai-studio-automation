"""Gemini vision screening for documentary shot-to-narration alignment.

This is a triage signal, not fact-checking or editorial approval. Human review
remains mandatory even when the model reports a likely match.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import time
from typing import Any

import requests

from core.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS


def _image_part(path: str | Path) -> dict[str, Any]:
    """Resize and JPEG-encode a sample frame to keep requests bounded."""
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((768, 768))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=78, optimize=True)
    return {"inlineData": {"mimeType": "image/jpeg",
                           "data": base64.b64encode(buffer.getvalue()).decode("ascii")}}


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    candidates = response.get("candidates") or []
    if not candidates:
        raise ValueError("Vision model returned no candidates.")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    raw = next((part.get("text") for part in parts if isinstance(part, dict) and part.get("text")), "")
    if not raw:
        raise ValueError("Vision model returned no text.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Vision model response was not valid JSON.")
        value = json.loads(raw[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("Vision model response must be a JSON object.")
    return value


def verify_shot_semantics(shot: dict[str, Any], *, api_key: str | None = None,
                          model: str | None = None, timeout: int = 90) -> dict[str, Any]:
    """Compare narration and visual intent; fall through configured free Gemini models.

    HTTP 429/5xx from one model must not immediately turn every shot into an
    error. This tries the configured fallback list without treating failures as
    positive visual matches. Human review remains mandatory.
    """
    key = (api_key if api_key is not None else GEMINI_API_KEY).strip()
    primary = (model or GEMINI_MODEL).strip()
    if not key:
        return {"status": "unavailable", "reason": "GEMINI_API_KEY is not configured.",
                "human_review_required": True}
    frames = [Path(value) for value in (shot.get("sample_frames") or [])[:3]]
    frames = [path for path in frames if path.is_file()]
    if not frames:
        return {"status": "unavailable", "reason": "No readable sample frames are available.",
                "human_review_required": True}

    narration = str(shot.get("narration") or "").strip()
    visual_prompt = str(shot.get("visual_prompt") or "").strip()
    if not narration or not visual_prompt:
        return {"status": "uncertain", "reason": "Narration or visual prompt is missing.",
                "human_review_required": True}

    prompt = (
        "You are a conservative documentary visual QA reviewer. Compare the attached "
        "sample frame(s) with the narration and intended visual description. Judge only "
        "what is visibly supported by the pixels. Do not infer a real date, identity, "
        "location, historical authenticity, factual truth, copyright status, or licence. "
        "Do not reward cinematic quality if the subject is wrong. If the frame is generic, "
        "ambiguous, contradictory, or insufficient to establish the described scene, choose "
        "uncertain or mismatch rather than guessing. Multiple frames are samples from the "
        "same shot. Return JSON only with keys: verdict (match|mismatch|uncertain), "
        "match_score (number 0 to 1), visible_evidence (short string), concerns (array of "
        "strings), and recommendation (short actionable string). A high score means the "
        "visible subject/action clearly aligns with the narration, not that the claim is true.\n\n"
        f"NARRATION:\n{narration[:1600]}\n\n"
        f"INTENDED VISUAL:\n{visual_prompt[:1200]}\n\n"
        f"VISUAL TYPE: {str(shot.get('visual_type') or 'unknown')}"
    )
    parts: list[dict[str, Any]] = [{"text": prompt}]
    try:
        parts.extend(_image_part(path) for path in frames)
        models = list(dict.fromkeys([primary] + [str(value).strip() for value in GEMINI_FALLBACK_MODELS if str(value).strip()]))
        transient_errors = []
        for index, model_id in enumerate(models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
            response = requests.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"contents": [{"parts": parts}],
                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": 700,
                                           "responseMimeType": "application/json"}},
                timeout=timeout,
            )
            status = getattr(response, "status_code", 200)
            if status == 429 or status in (500, 502, 503, 504):
                transient_errors.append(f"{model_id}: HTTP {status}")
                if index < len(models) - 1:
                    time.sleep(0.25)
                    continue
                return {
                    "status": "unavailable",
                    "reason": "All configured Gemini visual-review models were rate-limited or unavailable: " + "; ".join(transient_errors),
                    "attempted_models": models,
                    "human_review_required": True,
                }
            response.raise_for_status()
            data = _extract_json(response.json())
            verdict = str(data.get("verdict") or "uncertain").strip().lower()
            if verdict not in {"match", "mismatch", "uncertain"}:
                verdict = "uncertain"
            try:
                score = max(0.0, min(1.0, float(data.get("match_score"))))
            except (TypeError, ValueError):
                score = 0.0
                verdict = "uncertain"
            if verdict == "match" and score < 0.72:
                verdict = "uncertain"
            if verdict == "mismatch" and score > 0.55:
                verdict = "uncertain"
            concerns = data.get("concerns") or []
            if not isinstance(concerns, list):
                concerns = [str(concerns)]
            return {
                "status": verdict,
                "match_score": round(score, 3),
                "visible_evidence": str(data.get("visible_evidence") or "")[:700],
                "concerns": [str(item)[:300] for item in concerns[:8]],
                "recommendation": str(data.get("recommendation") or "")[:500],
                "checked_frame_count": len(frames),
                "model": model_id,
                "fallback_used": model_id != primary,
                "transient_model_errors": transient_errors,
                "human_review_required": True,
                "notice": "AI screening is fallible and does not verify factual truth, historical authenticity, identity, rights or licensing.",
            }
        return {"status": "unavailable", "reason": "No Gemini visual-review model was configured.",
                "human_review_required": True}
    except Exception as exc:
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:500],
                "checked_frame_count": len(frames), "human_review_required": True}
