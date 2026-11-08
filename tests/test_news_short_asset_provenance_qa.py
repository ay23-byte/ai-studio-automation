import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.news_short_asset_provenance_qa import audit_scene_asset_provenance


class SceneAssetProvenanceQATests(unittest.TestCase):
    def _fixture(self, root):
        asset = root / "scene.jpg"
        asset.write_bytes(b"approved source image bytes")
        sidecar = asset.with_suffix(".jpg.source.json")
        sidecar.write_text(json.dumps({
            "provider": "Example Archive",
            "asset_type": "photo",
            "page_url": "https://example.org/photo",
            "licence": "CC BY 4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution_text": "Example Archive / Photographer"
        }), encoding="utf-8")
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        register = root / "licenses.csv"
        with register.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "shot_id", "source", "use_status"
            ])
            writer.writeheader()
            writer.writerow({
                "shot_id": "shot_0001",
                "source": "Example Archive",
                "use_status": "eligible_pending_human_review"
            })
        manifest = root / "production_manifest.json"
        manifest.write_text(json.dumps({
            "shots": [{
                "shot_id": "shot_01",
                "source_ids": ["SRC-1"],
                "visual_type": "image"
            }],
            "visual_renderer": {"segments": [{
                "shot": 1,
                "asset_type": "image",
                "asset_path": str(asset),
                "source_sha256": digest
            }]},
            "licence_register": str(register)
        }), encoding="utf-8")
        return manifest, asset, register

    def test_exact_asset_and_register_pass_to_human_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _, _ = self._fixture(root)
            result = audit_scene_asset_provenance(manifest, root / "reports")
            self.assertEqual(result["status"], "REVIEW")
            self.assertEqual(result["verified_asset_count"], 1)
            self.assertTrue((root / "reports" / "scene_asset_provenance_report.json").is_file())
            self.assertFalse(result["publication_ready"])

    def test_changed_asset_hash_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, asset, _ = self._fixture(root)
            asset.write_bytes(b"changed after rendering")
            result = audit_scene_asset_provenance(manifest)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("SHA-256 differs" in error for error in result["errors"]))

    def test_missing_provenance_sidecar_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, asset, _ = self._fixture(root)
            asset.with_suffix(".jpg.source.json").unlink()
            result = audit_scene_asset_provenance(manifest)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("sidecar is missing" in error for error in result["errors"]))

    def test_blocked_licence_register_row_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _, register = self._fixture(root)
            rows = list(csv.DictReader(register.open(encoding="utf-8-sig")))
            rows[0]["use_status"] = "blocked"
            with register.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            result = audit_scene_asset_provenance(manifest)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("not eligible" in error for error in result["errors"]))

    def test_missing_scene_source_reference_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _, _ = self._fixture(root)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["shots"][0]["source_ids"] = []
            manifest.write_text(json.dumps(data), encoding="utf-8")
            result = audit_scene_asset_provenance(manifest)
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(any("source_ids" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
