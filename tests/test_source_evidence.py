import tempfile
import unittest
from pathlib import Path

from core.source_evidence import allocate_evidence_beat_durations, cited_source_records, research_source_records


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

    def test_evidence_screenshot_time_is_rebalanced_without_changing_chapter_length(self):
        beats = [
            {"visual_type": "stock"},
            {"visual_type": "chart", "source_refs": ["S1"]},
            {"visual_type": "stock"},
        ]
        durations = allocate_evidence_beat_durations(beats, [12.0, 0.5, 8.0])
        self.assertAlmostEqual(sum(durations), 20.5)
        self.assertGreaterEqual(durations[1], 4.2)
        self.assertTrue(all(value >= 1.0 for value in (durations[0], durations[2])))

    def test_multiple_evidence_beats_borrow_only_from_non_evidence_shots(self):
        beats = [{"visual_type": "document"}, {"visual_type": "timeline"}, {"visual_type": "stock"}]
        durations = allocate_evidence_beat_durations(beats, [0.5, 0.5, 12.0])
        self.assertGreaterEqual(durations[0], 4.2)
        self.assertGreaterEqual(durations[1], 4.2)
        self.assertGreaterEqual(durations[2], 1.0)

    def test_duration_rebalancing_rejects_mismatched_inputs(self):
        with self.assertRaises(ValueError):
            allocate_evidence_beat_durations([{"visual_type": "chart"}], [])

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
