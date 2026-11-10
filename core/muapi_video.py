"""Optional MuAPI text-to-video provider.

The endpoint and model-specific input schema are configured explicitly because
MuAPI model endpoints and required parameters vary. This provider is opt-in,
budget-guarded per request, and always records AI provenance for human review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE_URL = "https://api.muapi.ai/api/v1"


def _enabled(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _video_is_valid(path: Path) -> bool:
    try:
        from core.images import _video_ok
        return _video_ok(path)
    except Exception:
        return False


def generate_muapi_video(prompt: str, path: str | Path) -> bool:
    """Generate one AI B-roll clip via MuAPI; return False when disabled/unavailable."""
    token = os.getenv("MUAPI_API_KEY", "").strip()
    endpoint = os.getenv("MUAPI_VIDEO_ENDPOINT", "").strip().strip("/")
    if not (_enabled(os.getenv("MUAPI_VIDEO_ENABLED", "0")) and token and endpoint):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]+", endpoint):
        print("MuAPI video skipped: MUAPI_VIDEO_ENDPOINT must be a documented endpoint slug.")
        return False

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = os.getenv("MUAPI_VIDEO_INPUT_JSON", "").strip()
        payload = json.loads(raw) if raw else {"prompt": str(prompt)[:1800]}
        if not isinstance(payload, dict):
            raise ValueError("MUAPI_VIDEO_INPUT_JSON must be a JSON object.")
        payload["prompt"] = str(prompt)[:1800]

        headers = {"x-api-key": token, "Content-Type": "application/json"}
        response = requests.post(f"{BASE_URL}/{endpoint}", headers=headers,
                                 json=payload, timeout=60)
        response.raise_for_status()
        job = response.json()
        request_id = str(job.get("request_id") or "").strip()
        if not request_id:
            raise ValueError("MuAPI submit response did not include request_id.")

        cost = job.get("cost") if isinstance(job.get("cost"), dict) else {}
        amount = cost.get("amount_usd")
        max_cost = float(os.getenv("MUAPI_MAX_COST_USD", "0.25"))
        if amount is not None and float(amount) > max_cost:
            raise ValueError(
                f"MuAPI request quote ${float(amount):.4f} exceeds "
                f"MUAPI_MAX_COST_USD=${max_cost:.4f}; output was not downloaded."
            )

        deadline = time.time() + max(30, int(os.getenv("MUAPI_VIDEO_TIMEOUT_SECONDS", "600")))
        result = job
        while str(result.get("status") or "").lower() not in {"completed", "failed", "cancelled"}:
            if time.time() >= deadline:
                raise TimeoutError("MuAPI generation timed out while polling.")
            time.sleep(5)
            poll = requests.get(
                f"{BASE_URL}/predictions/{request_id}/result",
                headers={"x-api-key": token}, timeout=30,
            )
            poll.raise_for_status()
            result = poll.json()

        if str(result.get("status") or "").lower() != "completed":
            raise RuntimeError("MuAPI generation ended with status: " + str(result.get("status")))

        outputs = result.get("outputs") or result.get("output") or []
        candidates = outputs if isinstance(outputs, list) else [outputs]
        video_url = next((item for item in candidates
                          if isinstance(item, str) and urlparse(item).scheme == "https"), None)
        if not video_url:
            raise ValueError("MuAPI completed without a downloadable HTTPS output.")

        with requests.get(video_url, stream=True, timeout=180) as download:
            download.raise_for_status()
            with target.open("wb") as handle:
                for chunk in download.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if not _video_is_valid(target):
            target.unlink(missing_ok=True)
            raise ValueError("MuAPI output failed video/container/dimension validation.")

        final_cost = result.get("cost") if isinstance(result.get("cost"), dict) else cost
        metadata = {
            "provider": "MuAPI",
            "model_endpoint": endpoint,
            "request_id": request_id,
            "asset_type": "ai_generated_video",
            "ai_generated": True,
            "ai_disclosure": True,
            "page_url": "https://muapi.ai/",
            "licence_url": "https://muapi.ai/docs/api-reference",
            "licence": "Provider terms review required",
            "terms_reviewed": False,
            "licence_status": "AI-generated reconstruction; verify current MuAPI and underlying model terms before publication.",
            "cost_usd": (final_cost or {}).get("amount_usd") if isinstance(final_cost, dict) else None,
            "date_accessed": date.today().isoformat(),
            "prompt_sha256": hashlib.sha256(str(prompt).encode("utf-8")).hexdigest(),
            "human_visual_inspection_required": True,
            "historical_authenticity_verified": False,
        }
        target.with_suffix(target.suffix + ".source.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("  clip candidate ok (MuAPI AI video); AI reconstruction disclosure and human review required")
        return True
    except Exception as exc:
        target.unlink(missing_ok=True)
        print(f"MuAPI AI video generation failed: {type(exc).__name__}: {exc}")
        return False
