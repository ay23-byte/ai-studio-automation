"""Attach imported Wan2.1 assets to explicitly selected documentary review shots.

The mapping is editorial metadata only: it never approves visuals or clears rights.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
from pathlib import Path
from typing import Any


def map_wan_assets_to_storyboard(
    assets_path: str | Path,
    report_path: str | Path,
    mapping_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    assets_payload = json.loads(Path(assets_path).read_text(encoding="utf-8"))
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    mapping_payload = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    assets = assets_payload.get("assets")
    shots = report.get("shots")
    mappings = mapping_payload.get("mappings")
    if not isinstance(assets, list) or not isinstance(shots, list) or not isinstance(mappings, list) or not mappings:
        raise ValueError("assets, report shots and a non-empty mappings array are required")

    asset_by_scene = {}
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("scene_id"):
            raise ValueError("every imported asset must have a scene_id")
        scene_id = str(asset["scene_id"])
        if scene_id in asset_by_scene:
            raise ValueError(f"duplicate imported scene_id: {scene_id}")
        asset_by_scene[scene_id] = asset

    shot_by_id = {}
    for shot in shots:
        shot_id = str(shot.get("shot_id") or "")
        if not shot_id or shot_id in shot_by_id:
            raise ValueError("review report must contain unique, non-empty shot_id values")
        shot_by_id[shot_id] = shot

    used_scenes, used_shots = set(), set()
    for row in mappings:
        if not isinstance(row, dict):
            raise ValueError("each mapping must be an object")
        scene_id = str(row.get("scene_id") or "").strip()
        shot_id = str(row.get("shot_id") or "").strip()
        if not scene_id or not shot_id:
            raise ValueError("each mapping needs exact scene_id and shot_id values")
        if scene_id in used_scenes or shot_id in used_shots:
            raise ValueError("each scene and each storyboard shot may be mapped only once")
        if scene_id not in asset_by_scene:
            raise ValueError(f"unknown Wan scene_id: {scene_id}")
        if shot_id not in shot_by_id:
            raise ValueError(f"unknown storyboard shot_id: {shot_id}")
        asset = asset_by_scene[scene_id]
        media_path = Path(str(asset.get("path") or "")).resolve()
        if not media_path.is_file() or media_path.suffix.lower() != ".mp4":
            raise ValueError(f"Wan asset for {scene_id} is not an existing MP4")
        shot = shot_by_id[shot_id]
        shot["media_path"] = str(media_path)
        shot["media_type"] = "video"
        shot["visual_type"] = "ai_reconstruction"
        shot["ai_generated"] = True
        shot["wan_scene_id"] = scene_id
        shot["wan_generation_prompt"] = str(asset.get("prompt") or "")
        shot["source"] = {
            "provider": "Wan2.1 AI video generation",
            "asset_id": str(asset.get("asset_id") or f"wan_{scene_id}"),
            "model": "Wan2.1",
            "asset_type": "ai_generated_video",
            "licence": "AI-generated; no third-party source licence asserted",
            "licence_status": "not_reviewed",
            "page_url": "",
            "ai_generated": True,
        }
        shot["sample_frames"] = []
        shot["human_visual_review_required"] = True
        shot["historical_authenticity_verified"] = False
        used_scenes.add(scene_id)
        used_shots.add(shot_id)

    report["wan_asset_mapping"] = {
        "mapping_count": len(mappings),
        "mappings": [{"scene_id": row["scene_id"], "shot_id": row["shot_id"]} for row in mappings],
        "human_review_required": True,
        "publication_ready": False,
    }
    report["publication_ready"] = False
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Fingerprint changes invalidate any prior approval for the changed shot.
    from core.editorial_review import load_reviews
    load_reviews(target, shots)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, help="Imported wan_assets.json")
    parser.add_argument("--review-report", required=True, help="Existing visual_review_report.json")
    parser.add_argument("--mapping", required=True, help="JSON file with explicit scene_id/shot_id mappings")
    parser.add_argument("--output", required=True, help="New mapped review report path; source report is never overwritten")
    args = parser.parse_args()
    result = map_wan_assets_to_storyboard(args.assets, args.review_report, args.mapping, args.output)
    print(json.dumps({
        "mapped_count": result["wan_asset_mapping"]["mapping_count"],
        "output": str(Path(args.output)),
        "publication_ready": False,
        "review_status": "pending_human_review",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
