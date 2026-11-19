"""Milestone 7/8: licence validation and per-production credits."""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from src.production_v2.licenses import AssetLibrary, AssetRecord, validate_asset


class LicenseTests(unittest.TestCase):
    def test_allowed_licence_with_checksum_and_attribution(self):
        record = AssetRecord("a", "https://commons.wikimedia.org/wiki/File:x", "Creator",
                             "CC BY 4.0", "Creator, CC BY 4.0", sha256="abc")
        decision = validate_asset(record)
        self.assertTrue(decision.eligible, decision.reasons)

    def test_incomplete_and_blocked_records_fail_closed(self):
        self.assertEqual(validate_asset(AssetRecord("a", "", "", "")).status, "incomplete")
        blocked = AssetRecord("a", "https://example.org", "X", "All rights reserved", sha256="abc")
        self.assertEqual(validate_asset(blocked).status, "blocked")
        nc = AssetRecord("a", "https://example.org", "X", "CC BY-NC 4.0", sha256="abc")
        self.assertEqual(validate_asset(nc).status, "blocked")

    def test_synthetic_graphics_are_labelled_but_eligible(self):
        record = AssetRecord("synth", "generated-locally://v2", "v2", "synthetic demonstration graphic",
                             synthetic=True)
        self.assertTrue(validate_asset(record).eligible)
        self.assertIn("synthetic", record.license)

    def test_library_writes_licenses_csv_and_credits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = AssetLibrary()
            library.add(AssetRecord("a1", "https://example.org/a", "A", "CC BY 4.0",
                                    "A, CC BY 4.0", sha256="abc"))
            library.add(AssetRecord("a2", "https://example.org/b", "B", "CC0", sha256="def"))
            csv_path = library.write_licenses_csv(root / "licenses.csv")
            credits = library.write_credits(root / "credits.txt")
            rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
            self.assertEqual(len(rows), 2)
            self.assertIn("Attribution required", credits.read_text(encoding="utf-8"))
            self.assertTrue(library.all_eligible())


if __name__ == "__main__":
    unittest.main()
