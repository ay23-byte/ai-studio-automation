"""Per-video asset provenance for Shorts (documentaries already had a register).

Root cause addressed: Shorts recorded nothing about where each visual came
from, so there was no licence/attribution evidence and no rights QA at all.
"""
from __future__ import annotations

import json
from pathlib import Path


def _sidecar(path: Path) -> dict:
    sidecar = path.with_suffix(path.suffix + ".source.json")
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_shorts_asset_manifest(build_dir, scenes, media) -> dict:
    """Write ``asset_manifest.json``, ``licenses.csv`` and ``credits.txt``."""
    from core.asset_licensing import build_licence_register

    root = Path(build_dir)
    timeline, assets = [], []
    for index, item in enumerate(media):
        path = Path(item["path"])
        metadata = _sidecar(path)
        timeline.append({"_media": {"path": str(path), "type": item.get("type")},
                         "_start": float(index * 1000)})  # distinct scenes, not reuse
        assets.append({
            "scene": index + 1,
            "file": path.name,
            "media_type": item.get("type"),
            "acquired_via": item.get("provider") or "unknown",
            "provider": metadata.get("provider") or "Unknown source",
            "asset_type": metadata.get("asset_type") or item.get("type"),
            "licence": metadata.get("licence") or metadata.get("license") or "",
            "licence_url": metadata.get("licence_url") or metadata.get("license_url") or "",
            "source_url": metadata.get("page_url") or metadata.get("url") or "",
            "creator": metadata.get("creator") or metadata.get("author") or metadata.get("user") or "",
            "ai_generated": bool(metadata.get("ai_generated")
                                 or "ai_generated" in str(metadata.get("asset_type") or "").lower()),
            "fallback_card": metadata.get("fallback_kind") == "local_explainer_card",
            "provenance_recorded": bool(metadata),
            "narration": str((scenes[index] if index < len(scenes) else {}).get("say") or "")[:240],
        })
    register = build_licence_register(timeline, root)
    manifest = {
        "asset_count": len(assets),
        "assets": assets,
        "ai_generated_count": sum(1 for a in assets if a["ai_generated"]),
        "fallback_card_count": sum(1 for a in assets if a["fallback_card"]),
        "without_provenance": [a["file"] for a in assets if not a["provenance_recorded"]],
        "licence_register": {key: register[key] for key in
                             ("asset_count", "eligible_pending_human_review", "blocked_count",
                              "blocked_assets", "publication_ready", "notice")},
        "contains_synthetic_media": any(a["ai_generated"] for a in assets),
    }
    (root / "asset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
