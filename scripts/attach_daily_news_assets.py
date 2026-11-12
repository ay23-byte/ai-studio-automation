"""Attach reviewed scene assets from a prior Actions artifact to an approved script."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _artifact_file(artifact_root: Path, recorded: str) -> Path:
    # Asset preparation records paths under output/daily_news/asset_review/.
    # The uploaded artifact root starts at asset_review/, so resolve by safe suffix.
    parts = Path(recorded).parts
    if Path(recorded).is_absolute() or ".." in parts:
        raise ValueError(f"Unsafe recorded asset path: {recorded}")
    suffix = Path(*parts[-2:]) if len(parts) >= 2 else Path(recorded)
    matches = list(artifact_root.rglob(suffix.name))
    matches = [p for p in matches if p.is_file() and p.parent.name == suffix.parent.name]
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one artifact asset for {recorded}; found {len(matches)}.")
    resolved = matches[0].resolve()
    if artifact_root.resolve() not in resolved.parents:
        raise ValueError("Artifact asset resolved outside the downloaded artifact.")
    return resolved


def attach_assets(artifact_dir: str | Path, approved_script_path: str | Path,
                  output_script_path: str | Path, asset_output_dir: str | Path) -> Path:
    artifact_root = Path(artifact_dir).resolve()
    approved = read_json(Path(approved_script_path))
    candidate = read_json(artifact_root / "production_candidate.json")
    manifest = read_json(artifact_root / "asset_review_manifest.json")
    if approved.get("editorial_status") != "approved_for_production":
        raise ValueError("Approved script must have editorial_status=approved_for_production.")
    approved_scenes = approved.get("scenes")
    candidate_scenes = candidate.get("scenes")
    inventory = manifest.get("assets")
    if not isinstance(approved_scenes, list) or not isinstance(candidate_scenes, list):
        raise ValueError("Approved script or prepared candidate has no scenes list.")
    if not 4 <= len(approved_scenes) <= 6 or len(approved_scenes) != len(candidate_scenes):
        raise ValueError("Approved script must match the prepared 4–6 scene asset inventory.")
    if not isinstance(inventory, list) or len(inventory) != len(candidate_scenes):
        raise ValueError("Asset-review manifest does not match the prepared candidate.")

    target_root = Path(asset_output_dir).resolve()
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True)
    attached = json.loads(json.dumps(approved))
    for index, (scene, prepared, row) in enumerate(zip(attached["scenes"], candidate_scenes, inventory), 1):
        approved_refs = sorted(map(str, scene.get("source_ids") or []))
        prepared_refs = sorted(map(str, prepared.get("source_ids") or []))
        if approved_refs != prepared_refs:
            raise ValueError(f"Scene {index} source_ids differ from the human-reviewed prepared draft.")
        if str(scene.get("say") or "").strip() != str(prepared.get("say") or "").strip():
            raise ValueError(f"Scene {index} narration differs from the asset-reviewed draft.")
        recorded = str(prepared.get("asset_path") or row.get("path") or "")
        source = _artifact_file(artifact_root, recorded)
        source_sidecar = source.with_suffix(source.suffix + ".source.json")
        if not source_sidecar.is_file():
            raise FileNotFoundError(f"Scene {index} provenance sidecar is missing.")
        name = f"scene_{index:02d}{source.suffix.lower()}"
        destination = target_root / name
        shutil.copy2(source, destination)
        shutil.copy2(source_sidecar, destination.with_suffix(destination.suffix + ".source.json"))
        scene["asset_path"] = destination.as_posix()
        scene["asset_type"] = prepared.get("asset_type") or row.get("type")
        scene["ai_disclosure"] = prepared.get("ai_disclosure", row.get("ai_disclosure_required", False))
    out = Path(output_script_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(attached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--approved-script", required=True)
    parser.add_argument("--output-script", required=True)
    parser.add_argument("--asset-output-dir", required=True)
    args = parser.parse_args()
    result = attach_assets(args.artifact_dir, args.approved_script, args.output_script, args.asset_output_dir)
    print(f"Approved script connected to downloaded visual artifact: {result}")


if __name__ == "__main__":
    main()
