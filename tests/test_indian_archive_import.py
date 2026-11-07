import json
import os
import tempfile
import unittest
from pathlib import Path

from core.asset_licensing import licence_is_usable
from core.footage_sources import register_indian_archive_asset


class IndianArchiveImportTests(unittest.TestCase):
    def test_ncaa_import_records_written_permission_and_youtube_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"archive-video")
            previous = Path.cwd()
            try:
                os.chdir(root)
                target, sidecar = register_indian_archive_asset(
                    source, provider="NCAA", title="Indian folk performance",
                    source_url="https://ncaa.gov.in/repository/item/123",
                    licence_reference="IGNCA permission email 2026-11-01",
                    usage_scope="Commercial YouTube use, including monetization",
                    creator="Partner institution", attribution="Courtesy IGNCA",
                )
                self.assertTrue(target.is_file())
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertEqual(data["provider"], "NCAA")
                self.assertEqual(data["licence"], "NCAA written permission")
                self.assertTrue(licence_is_usable(data)[0])
            finally:
                os.chdir(previous)

    def test_prasar_bharati_import_rejects_noncommercial_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "clip.mp4"
            source.write_bytes(b"archive-video")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(ValueError, "commercial/monetized YouTube"):
                    register_indian_archive_asset(
                        source, provider="Prasar Bharati Archives",
                        title="Republic Day parade",
                        source_url="https://prasarbharati.gov.in/archive/item/1",
                        licence_reference="PB licence 2026/123",
                        usage_scope="Non-commercial educational use only",
                    )
            finally:
                os.chdir(previous)

    def test_indian_archive_requires_official_item_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.mp4"
            source.write_bytes(b"archive-video")
            with self.assertRaisesRegex(ValueError, "official provider item page"):
                register_indian_archive_asset(
                    source, provider="NCAA", title="Test",
                    source_url="https://example.com/video",
                    licence_reference="permission-1",
                    usage_scope="Commercial YouTube use",
                )

    def test_permission_label_without_document_reference_is_blocked(self):
        ok, reason = licence_is_usable({
            "licence": "NCAA written permission",
            "usage_scope": "Commercial YouTube use",
        })
        self.assertFalse(ok)
        self.assertIn("reference", reason)


if __name__ == "__main__":
    unittest.main()
