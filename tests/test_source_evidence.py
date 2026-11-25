import tempfile
import unittest
from pathlib import Path

from core.source_evidence import cited_source_records, research_source_records


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"source_id": "S1", "title": "Official report", "publisher": "Agency",
             "published_at": "2026-01-02", "url": "https://agency.example/report"},
            {"source_id": "S2", "title": "News article", "publisher": "News",
             "url": "https://news.example/story"},
            {"source_id": "S3", "title": "Bad URL", "publisher": "Bad",
             "url": "javascript:alert(1)"},
        ]

    def test_script_source_compatibility_uses_legacy_sources_when_new_field_empty(self):
        script = {"research_sources": [], "sources": self.sources}
        self.assertEqual(research_source_records(script), self.sources)

    def test_script_source_compatibility_prefers_populated_research_sources(self):
        preferred = [self.sources[0]]
        script = {"research_sources": preferred, "sources": self.sources}
        self.assertEqual(research_source_records(script), preferred)

    def test_script_source_compatibility_handles_missing_and_malformed_fields(self):
        self.assertEqual(research_source_records({}), [])
        self.assertEqual(research_source_records({"sources": "not-a-list"}), [])

    def test_only_explicitly_cited_valid_sources_are_selected(self):
        beat = {"visual_type": "chart", "source_refs": ["S1"],
                "chart_spec": {"source_refs": ["S2", "S3", "UNKNOWN"]}}
        rows = cited_source_records(beat, self.sources)
        self.assertEqual([row["source_id"] for row in rows], ["S1", "S2"])
        self.assertEqual(rows[0]["publisher"], "Agency")

    def test_timeline_source_references_are_included(self):
        beat = {"visual_type": "timeline",
                "timeline_events": [{"date": "2025", "source_refs": ["S2"]}]}
        rows = cited_source_records(beat, self.sources)
        self.assertEqual([row["source_id"] for row in rows], ["S2"])

    def test_missing_or_invalid_references_do_not_create_fake_sources(self):
        rows = cited_source_records({"source_refs": ["MISSING", "S3"]}, self.sources)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
