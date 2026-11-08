"""Verify each rendered news-Short scene is traceable to its exact source asset.

This is metadata/integrity screening only. It does not prove copyright ownership,
licence scope, factual context, or editorial relevance; human review remains required.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


PROJECT_GRAPHIC_PROVIDER = "LabLens/AI Studio local graphic renderer"
ELIGIBLE_STATUSES = {
    "eligible_pending_human_review",
    "ai_reconstruction_disclosure_and_terms_review",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_scene_asset_provenance(manifest_path: str | Path,
                                 report_dir: str | Path | None = None) -> dict:
    manifest_path = Path(manifest_path)
    errors: list[str] = []
    warnings: list[str] = []
    scenes: list[dict] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shots = manifest.get("shots")
        segments = (manifest.get("visual_renderer") or {}).get("segments")
        if not isinstance(shots, list) or not shots:
            raise ValueError("Production manifest has no shots.")
        if not isinstance(segments, list) or len(segments) != len(shots):
            raise ValueError("Rendered media segment count does not match production scene count.")
        register_value = manifest.get("licence_register")
        if not register_value:
            raise ValueError("Production manifest does not reference a licence register.")
        register_path = Path(register_value)
        if not register_path.is_absolute():
            register_path = manifest_path.parent / register_path
        if not register_path.is_file():
            raise FileNotFoundError(f"Licence register is missing: {register_path}")
        with register_path.open(newline="", encoding="utf-8-sig") as handle:
            register_rows = list(csv.DictReader(handle))
    except Exception as exc:
        return {
            "status": "FAIL",
            "errors": [f"{type(exc).__name__}: {exc}"],
            "warnings": [],
            "scene_count": 0,
            "human_review_required": True,
            "publication_ready": False,
        }

    if len(register_rows) != len(shots):
        errors.append(
            f"Licence register has {len(register_rows)} rows; expected {len(shots)}."
        )

    for index, (shot, segment) in enumerate(zip(shots, segments), 1):
        scene_errors: list[str] = []
        asset_value = segment.get("asset_path")
        asset = Path(str(asset_value or ""))
        if not asset.is_absolute():
            asset = manifest_path.parent / asset
        if not asset.is_file():
            scene_errors.append(f"Rendered source asset is missing: {asset}")
            actual_hash = None
        else:
            actual_hash = _sha256(asset)
            expected_hash = str(segment.get("source_sha256") or "")
            if not expected_hash:
                scene_errors.append("Rendered segment has no source SHA-256.")
            elif actual_hash.lower() != expected_hash.lower():
                scene_errors.append("Source asset SHA-256 differs from the hash recorded at render time.")

        sidecar = asset.with_suffix(asset.suffix + ".source.json")
        metadata = {}
        if not sidecar.is_file():
            scene_errors.append(f"Source provenance sidecar is missing: {sidecar}")
        else:
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict):
                    raise ValueError("sidecar root must be an object")
            except (OSError, ValueError) as exc:
                scene_errors.append(f"Source provenance sidecar is invalid: {exc}")
                metadata = {}

        provider = str(metadata.get("provider") or "").strip()
        asset_type = str(metadata.get("asset_type") or segment.get("asset_type") or "").strip()
        is_ai = "ai_generated" in asset_type.lower()
        is_project_graphic = (
            provider == PROJECT_GRAPHIC_PROVIDER
            and asset_type == "programmatic_graphic"
            and metadata.get("ai_generated") is False
        )
        if not provider:
            scene_errors.append("Source sidecar has no provider.")
        if is_ai:
            if shot.get("ai_disclosure") is not True:
                scene_errors.append("AI-generated scene is missing an explicit disclosure flag.")
            if metadata.get("terms_reviewed") is not True:
                scene_errors.append("AI-generated asset terms are not marked reviewed.")
        elif is_project_graphic:
            if not all(metadata.get(key) for key in (
                "licence", "font_licence", "font_licence_url", "attribution_text"
            )):
                scene_errors.append("Project graphic is missing project/font licence or attribution metadata.")
        else:
            if not (metadata.get("page_url") or metadata.get("url")):
                scene_errors.append("Real media sidecar has no source page URL.")
            if not (metadata.get("licence") or metadata.get("license")):
                scene_errors.append("Real media sidecar has no licence label.")
            if not (metadata.get("licence_url") or metadata.get("license_url")):
                scene_errors.append("Real media sidecar has no licence URL.")

        register_row = register_rows[index - 1] if index <= len(register_rows) else {}
        expected_shot_id = f"shot_{index:04d}"
        if register_row.get("shot_id") != expected_shot_id:
            scene_errors.append(
                f"Licence register row is not mapped to {expected_shot_id}."
            )
        if register_row.get("use_status") not in ELIGIBLE_STATUSES:
            scene_errors.append(
                f"Licence register status is not eligible for human review: {register_row.get('use_status') or 'missing'}."
            )
        if register_row.get("source") and provider and register_row["source"] != provider:
            scene_errors.append("Licence register provider does not match the asset sidecar.")

        if not shot.get("source_ids"):
            scene_errors.append("Scene has no linked editorial source_ids.")
        if shot.get("visual_type") and str(shot["visual_type"]) != str(segment.get("asset_type")):
            scene_errors.append("Scene visual_type does not match its rendered media segment.")

        scenes.append({
            "scene": index,
            "asset_path": str(asset),
            "provider": provider or None,
            "asset_type": asset_type or None,
            "expected_sha256": segment.get("source_sha256"),
            "actual_sha256": actual_hash,
            "hash_matches": bool(actual_hash and segment.get("source_sha256")
                                 and actual_hash.lower() == str(segment["source_sha256"]).lower()),
            "licence_register_status": register_row.get("use_status"),
            "errors": scene_errors,
        })
        errors.extend(f"Scene {index}: {message}" for message in scene_errors)

    status = "FAIL" if errors else "REVIEW"
    report = {
        "status": status,
        "scene_count": len(shots),
        "verified_asset_count": sum(not row["errors"] for row in scenes),
        "scenes": scenes,
        "errors": errors,
        "warnings": warnings,
        "human_review_required": True,
        "publication_ready": False,
        "notice": (
            "A clean provenance audit confirms file identity and metadata consistency only. "
            "It does not prove ownership, licence interpretation, permitted commercial use, "
            "source-to-claim support, visual relevance, or legal compliance. A human must "
            "inspect the original source pages and complete rendered Short."
        ),
    }
    if report_dir is not None:
        root = Path(report_dir)
        root.mkdir(parents=True, exist_ok=True)
        (root / "scene_asset_provenance_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report
