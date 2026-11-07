"""Prepare illustrative/licensed visual candidates for a reviewed news Short.

This stage never renders the final MP4 or uploads/publishes to YouTube.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def find_one(root: str | Path, filename: str) -> Path:
    matches = sorted(Path(root).rglob(filename))
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {filename}; found {len(matches)}.")
    return matches[0]


def validate_draft(packet: dict, script: dict) -> None:
    if packet.get("review_status") != "approved":
        raise ValueError("Evidence packet must be approved before asset preparation.")
    sources = packet.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Evidence packet must contain sources.")
    if not isinstance(packet.get("verified_facts"), list) or not packet["verified_facts"]:
        raise ValueError("Evidence packet must contain verified facts.")
    if script.get("editorial_status") != "draft_requires_human_script_and_visual_review":
        raise ValueError("Expected the generated human-review script draft.")
    scenes = script.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 6:
        raise ValueError("News Short must contain 4–6 scenes.")
    known = {str(item.get("source_id")) for item in sources if item.get("source_id")}
    for number, scene in enumerate(scenes, start=1):
        for key in ("say", "caption", "image"):
            if not str(scene.get(key) or "").strip():
                raise ValueError(f"Scene {number} is missing {key}.")
        refs = scene.get("source_ids")
        if not isinstance(refs, list) or not refs or not set(map(str, refs)).issubset(known):
            raise ValueError(f"Scene {number} must cite known source IDs.")


def prepare_candidate_script(script: dict, packet: dict, scene_assets: list[dict]) -> dict:
    candidate = dict(script)
    candidate["sources"] = packet["sources"]
    candidate["language"] = "hindi"
    candidate["voice_gender"] = "female"
    candidate["editorial_status"] = "draft_assets_require_human_visual_and_rights_review"
    candidate["visual_review_status"] = "pending_human_visual_and_rights_review"
    candidate["scenes"] = [
        {**scene, **asset}
        for scene, asset in zip(script["scenes"], scene_assets)
    ]
    return candidate


def prepare_assets(artifact_dir: str | Path, output_dir: str | Path) -> dict:
    artifact_dir, output_dir = Path(artifact_dir), Path(output_dir)
    packet = json.loads(find_one(artifact_dir, "reviewed_story.json").read_text(encoding="utf-8"))
    script = json.loads(find_one(artifact_dir, "shorts_script_draft.json").read_text(encoding="utf-8"))
    validate_draft(packet, script)

    from core.images import fetch_clip, generate_image
    from core.asset_licensing import build_licence_register

    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    used_ids: set = set()
    scene_assets = []
    timeline = []

    for index, scene in enumerate(script["scenes"], start=1):
        prompt = (
            "Illustrative, non-archival news documentary visual for this exact narration. "
            "Do not depict a real identifiable person, do not imitate a news broadcast, "
            "and do not add text, labels, logos, maps with invented boundaries, or unsupported details. "
            f"Narration: {scene['say']} Visual direction: {scene['image']}. "
            "Vertical 9:16 composition."
        )
        keywords = scene.get("keywords") or [scene.get("image", "")]
        clip_path = assets_dir / f"scene_{index:02d}.mp4"
        image_path = assets_dir / f"scene_{index:02d}.jpg"
        if fetch_clip(keywords, clip_path, used_ids, scene_prompt=prompt):
            asset_type, asset_path = "video", clip_path
            ai_disclosure = False
        elif generate_image(prompt, image_path, category="general"):
            asset_type, asset_path = "image", image_path
            ai_disclosure = True
        else:
            raise RuntimeError(
                f"No visual candidate could be prepared for scene {index}. "
                "The pipeline refuses to insert unrelated filler footage."
            )

        metadata_path = asset_path.with_suffix(asset_path.suffix + ".source.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        scene_assets.append({
            "asset_type": asset_type,
            "asset_path": str(asset_path),
            "ai_disclosure": ai_disclosure,
            "asset_review_status": "pending_human_visual_and_rights_review",
        })
        timeline.append({
            "_start": float((index - 1) * 5),
            "_media": {"path": str(asset_path), "type": asset_type},
            "visual_prompt": scene.get("image", ""),
            "narration": scene.get("say", ""),
            "on_screen_text": scene.get("caption", ""),
            "source_refs": scene.get("source_ids", []),
        })
        print(f"Prepared scene {index}: {asset_type} via {metadata.get('provider', 'unknown provider')}")

    licence = build_licence_register(timeline, output_dir)
    candidate = prepare_candidate_script(script, packet, scene_assets)
    (output_dir / "production_candidate.json").write_text(
        json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "reviewed_evidence_packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "title": script.get("title"),
        "scene_count": len(scene_assets),
        "assets": [
            {
                "scene": index,
                "type": item["asset_type"],
                "path": item["asset_path"],
                "ai_disclosure_required": item["ai_disclosure"],
                "review_status": item["asset_review_status"],
            }
            for index, item in enumerate(scene_assets, start=1)
        ],
        "licence_register": licence,
        "editorial_status": candidate["editorial_status"],
        "visual_review_status": candidate["visual_review_status"],
        "final_mp4_rendered": False,
        "youtube_upload_performed": False,
        "publication_performed": False,
        "next_gate": (
            "Human must inspect every asset, verify the exact source/licence terms and attribution, "
            "confirm AI disclosure where applicable, then separately approve final rendering."
        ),
    }
    (output_dir / "asset_review_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare news Short visuals for human review; no final render.")
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--output-dir", default="output/daily_news/asset_review")
    args = parser.parse_args()
    prepare_assets(args.artifact_dir, args.output_dir)


if __name__ == "__main__":
    main()
