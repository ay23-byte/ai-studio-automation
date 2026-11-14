"""Load optional editorial profiles for script generation.

Profiles add channel-specific positioning and audience context to the existing
script prompts. The default/standard profile is a no-op, preserving legacy runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = ROOT / "config" / "documentary_channel_strategy.json"


def load_channel_profile(profile_name: str | None = None) -> dict:
    name = str(profile_name if profile_name is not None else os.getenv("CHANNEL_PROFILE", "standard")).strip().lower()
    if name in {"", "standard", "default", "off", "none"}:
        return {}
    if name != "beyond_science":
        raise ValueError(f"Unknown CHANNEL_PROFILE {name!r}; use 'standard' or 'beyond_science'.")
    try:
        data = json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Could not load documentary channel strategy: {exc}") from exc
    if not isinstance(data, dict) or data.get("strategy_name") != "Beyond Science — World Explained":
        raise ValueError("Documentary channel strategy file is missing or has an unexpected identity.")
    pillars = data.get("content_pillars") or []
    pillar_lines = [
        f"- {row.get('id')}: {row.get('description')}"
        for row in pillars if isinstance(row, dict) and row.get("description")
    ]
    brand_voice = "\n".join([
        f"Series identity: {data.get('strategy_name')}.",
        f"Channel positioning: {data.get('positioning')}.",
        f"Audience transition: {data.get('audience_bridge')}.",
        "Content pillars:",
        *pillar_lines,
        "Storytelling: use a curiosity-led opening question, clear promise, historical/geographic context, "
        "named and documented turning points, a step-by-step explanation of the mechanism, human "
        "consequences, counterarguments, uncertainty and an evidence-led resolution.",
        "Keep the existing Engineering Physics audience in mind: prioritize bridge subjects connecting "
        "science, engineering, technology and global affairs before moving to unrelated political topics.",
        "Use original wording, research, visual concepts and packaging. Never imitate another creator's "
        "scripts, branding, thumbnails, editing signature or assets.",
        "Do not sacrifice factual accuracy, source traceability, rights review or the existing human release gates.",
    ])
    return {
        "profile_id": name,
        "channel_name": data.get("channel_name", "Engineering Physics"),
        "audience": "Curious Indian viewers interested in science, engineering, technology and how global systems work; explain complex ideas accessibly without assuming specialist knowledge.",
        "brand_voice": brand_voice,
    }


def apply_configured_channel_profile(channel_name: str = "", audience: str = "",
                                     brand_voice: str = "") -> tuple[str, str, str]:
    profile = load_channel_profile()
    return (
        channel_name or str(profile.get("channel_name") or ""),
        audience or str(profile.get("audience") or ""),
        brand_voice or str(profile.get("brand_voice") or ""),
    )
