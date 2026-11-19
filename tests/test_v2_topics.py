"""Milestone 3: topic/story history deduplication."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_v2.topics import TopicHistory, similarity


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = TopicHistory(Path(self.tmp.name) / "history.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_similarity_ignores_stopwords(self):
        self.assertEqual(similarity("The Moon Landing", "Moon Landing"), 1.0)
        self.assertLess(similarity("Moon landing", "Quantum tunnelling"), 0.2)

    def test_exact_and_near_duplicates_are_detected(self):
        self.history.record("Chandrayaan-3 lunar landing", "news_short", "UPLOADED_PRIVATE")
        self.assertTrue(self.history.is_duplicate("Chandrayaan-3 lunar landing", "news_short"))
        self.assertTrue(self.history.is_duplicate("Chandrayaan-3 lunar landing explained", "news_short"))
        self.assertFalse(self.history.is_duplicate("Chandrayaan-3 lunar landing", "physics_short"))

    def test_candidate_selection_skips_duplicates_and_unresearchable_topics(self):
        self.history.record("Black hole shadows", "physics_short")
        candidates = [
            {"title": "Black hole shadows", "research_available": True},
            {"title": "Unverifiable rumour", "research_available": False},
            {"title": "Quantum tunnelling in semiconductors", "research_available": True},
        ]
        chosen = self.history.suitable_topic(candidates, "physics_short")
        self.assertEqual(chosen["title"], "Quantum tunnelling in semiconductors")

    def test_history_survives_reload(self):
        self.history.record("A new topic", "documentary", "PLANNED")
        reloaded = TopicHistory(Path(self.tmp.name) / "history.json")
        self.assertTrue(reloaded.is_duplicate("A new topic", "documentary"))


if __name__ == "__main__":
    unittest.main()
