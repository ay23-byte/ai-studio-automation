"""Import reviewed Wan2.1 worker outputs as explicitly unapproved documentary assets.

This adapter copies generated clips into a job media directory and writes a
machine-readable asset manifest. It never marks AI footage as archival, licensed,
or human-approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def import_wan_manifest(manifest_path: str | Path, clips_root: str | Path,
                        output_dir: str | Path) -> dict:
    manifest_path = Path(manifest_path)
    clips_root = Path(clips_root).resolve()
    output_dir = Path(output_dir)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene manifest must contain a non-empty scenes array")

    output_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    seen = set()
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"scene {index} must be an object")
        scene_id = str(scene.get("id") or "").strip()
        filename = str(scene.get("file") or "").strip()
        prompt = str(scene.get("prompt") or "").strip()
        if not scene_id or not filename or not prompt:
            raise ValueError(f"scene {index} needs id, file and prompt")
        if scene_id in seen:
            raise ValueError(f"duplicate scene id: {scene_id}")
        seen.add(scene_id)

        source = (clips_root / filename).resolve()
        if source.parent != clips_root or source.suffix.lower() != ".mp4":
            raise ValueError(f"scene {scene_id}: file must be a direct-child MP4 filename")
        if not source.is_file() or source.stat().st_size <= 50_000:
            raise ValueError(f"scene {scene_id}: MP4 is missing or too small: {filename}")

        destination_name = f"{index:02d}_{scene_id.replace('/', '_')}.mp4"
        destination = output_dir / destination_name
        shutil.copy2(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        assets.append({
            "asset_id": f"wan_{scene_id}",
            "scene_id": scene_id,
            "scene_number": index,
            "path": str(destination),
            "provider": "Wan2.1 AI video generation",
            "source": "Wan2.1 AI video generation",
            "page_url": "",
            "licence": "AI-generated; no third-party source licence asserted",
            "licence_reviewed": False,
            "human_visual_reviewed": False,
            "visual_type": "ai_reconstruction",
            "ai_generated": True,
            "disclosure_ready": False,
            "prompt": prompt,
            "title": str(scene.get("title") or scene_id),
            "sha256": digest,
            "bytes": destination.stat().st_size,
            "duration_seconds": scene.get("duration_seconds"),
            "width": scene.get("width"),
            "height": scene.get("height"),
            "review_status": "pending_human_review",
        })

    result = {
        "schema_version": 1,
        "source_manifest": manifest_path.name,
        "asset_count": len(assets),
        "assets": assets,
        "publication_ready": False,
        "notice": (
            "All imported clips are AI-generated illustrative assets. Human visual review, "
            "AI disclosure, factual-context review and final editorial approval are required."
        ),
    }
    (output_dir / "wan_assets.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Wan2.1 MP4 outputs into a documentary review package.")
    parser.add_argument("--manifest", required=True, help="Kaggle scene_manifest.json")
    parser.add_argument("--clips-dir", required=True, help="Directory containing generated MP4 files")
    parser.add_argument("--output-dir", required=True, help="Destination media directory")
    args = parser.parse_args()
    result = import_wan_manifest(args.manifest, args.clips_dir, args.output_dir)
    print(json.dumps({
        "asset_count": result["asset_count"],
        "output_dir": str(Path(args.output_dir)),
        "publication_ready": False,
        "review_status": "pending_human_review",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
