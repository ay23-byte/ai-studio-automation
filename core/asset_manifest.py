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


def write_shorts_asset_manifest(build_dir, scenes, media, *, starts=None) -> dict:
    """Write ``asset_manifest.json``, ``licenses.csv`` and ``credits.txt``."""
    from core.asset_licensing import build_licence_register

    root = Path(build_dir)
    timeline, assets = [], []
    for index, item in enumerate(media):
        path = Path(item["path"])
        metadata = _sidecar(path)
        timeline.append({"_media": {"path": str(path), "type": item.get("type")},
                         "_start": float(starts[index]) if starts is not None else float(index)})
        from core.review_contract import sha256_file
        assets.append({
            "sha256": sha256_file(path), "job": root.name, "asset_id": f"{root.name}:scene_{index+1}",
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


def supplied_scene(directory, index, script, build_dir):
    """An explicitly supplied scene pack is all-or-nothing; model output cannot select host files."""
    if not directory:
        return None
    import shutil
    from core.jobs import stable_hash
    from core.review_contract import inside, read_json, sha256_file, write_json
    root = Path(directory)
    pack = read_json(root / 'scene_pack.json')
    rows = pack.get('scenes')
    if pack.get('script_hash') != stable_hash(script) or not isinstance(rows, list) or len(rows) != len(script['scenes']):
        raise ValueError('Scene pack does not match the supplied script or scene count.')
    row = rows[index]
    source = inside(root, row['file'])
    sidecar = inside(root, row['file'] + '.source.json')
    meta = read_json(sidecar, what='asset provenance')
    if source.suffix.lower() not in {'.mp4', '.png', '.jpg'} or sha256_file(source) != row.get('sha256'):
        raise ValueError(f'Invalid supplied asset type or SHA-256 for scene {index+1}.')
    kind = 'video' if source.suffix.lower() == '.mp4' else 'image'
    dest = build_dir / (('raw' if kind == 'video' else 'img') + str(index) + source.suffix.lower())
    if not dest.exists() or sha256_file(dest) != row['sha256']:
        shutil.copyfile(source, dest)
    write_json(dest.with_suffix(dest.suffix+'.source.json'), meta)
    return {'type': kind, 'path': dest, 'provider': 'supplied', 'sha256': row['sha256']}
