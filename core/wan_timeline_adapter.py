"""Apply explicitly mapped Wan2.1 review assets to a rebuilt documentary timeline.

This is a second-pass adapter: it does not generate, approve, license, render or publish
assets. It verifies that mapped shots still identify the same narration beat before
replacing only their media source.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def apply_mapped_wan_review(shot_timeline: list[dict[str, Any]],
                            review_report_path: str | Path) -> dict[str, Any]:
    """Apply mapped Wan2.1 clips by stable review shot ID after identity checks."""
    report_path = Path(review_report_path)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    rows = payload.get("shots")
    summary = payload.get("wan_asset_mapping") or {}
    if not isinstance(rows, list) or not isinstance(summary, dict):
        raise ValueError("Mapped Wan review report must contain shots and wan_asset_mapping.")
    if not summary.get("human_review_required", True) or summary.get("publication_ready") is True:
        raise ValueError("Wan mapping must retain mandatory human review and publication block.")

    applied = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("wan_scene_id"):
            continue
        shot_id = str(row.get("shot_id") or "")
        if not shot_id.startswith("shot_") or not shot_id[5:].isdigit():
            raise ValueError(f"Invalid mapped shot_id: {shot_id!r}")
        index = int(shot_id[5:]) - 1
        if index < 0 or index >= len(shot_timeline):
            raise ValueError(f"Mapped shot {shot_id} does not exist in the rebuilt timeline.")
        target = shot_timeline[index]
        narration = str(row.get("narration") or "").strip()
        current_narration = str(target.get("narration") or "").strip()
        if not narration or narration != current_narration:
            raise ValueError(
                f"Mapped shot {shot_id} no longer matches the timeline narration; "
                "rebuild the mapping against the current documentary."
            )
        chapter = str(row.get("chapter_id") or "unassigned")
        current_chapter = str(target.get("chapter_id") or target.get("chapter") or
                              target.get("_chapter_index") or "unassigned")
        if chapter != current_chapter:
            raise ValueError(f"Mapped shot {shot_id} chapter identity changed; remap the asset.")
        path = Path(str(row.get("media_path") or "")).resolve()
        if not path.is_file() or path.suffix.lower() != ".mp4":
            raise ValueError(f"Mapped Wan clip for {shot_id} is missing or is not an MP4.")
        if not row.get("wan_generation_prompt"):
            raise ValueError(f"Mapped Wan clip for {shot_id} has no generation prompt.")
        source = row.get("source")
        if not isinstance(source, dict) or not source.get("ai_generated"):
            raise ValueError(f"Mapped Wan clip for {shot_id} is missing AI provenance.")
        target["_media"] = {"type": "video", "path": str(path)}
        target["visual_type"] = "ai_reconstruction"
        target["ai_generated"] = True
        target["wan_scene_id"] = str(row["wan_scene_id"])
        target["wan_generation_prompt"] = str(row["wan_generation_prompt"])
        target["source"] = source
        target["human_visual_review_required"] = True
        target["historical_authenticity_verified"] = False
        applied.append({"shot_id": shot_id, "scene_id": str(row["wan_scene_id"]),
                        "media_path": str(path)})

    if not applied:
        raise ValueError("Mapped review report contains no Wan2.1 shots to apply.")
    return {
        "applied_count": len(applied),
        "applied": applied,
        "human_review_required": True,
        "rights_review_required": True,
        "publication_ready": False,
    }
