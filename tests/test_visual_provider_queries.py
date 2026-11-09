"""Regression tests for concise visual-provider search queries."""
import unittest

from core.images import _compact_search_query
from core.archival_images import _search_query


class VisualProviderQueryTests(unittest.TestCase):
    def test_pixabay_query_is_capped_at_documented_limit(self):
        query = _compact_search_query("Leningrad Nuclear Power Plant construction in the mid-1970s " * 4, 100)
        self.assertLessEqual(len(query), 100)
        self.assertTrue(query.startswith("Leningrad Nuclear Power Plant"))

    def test_archival_query_extracts_specific_context(self):
        prompt = (
            "Create a documentary shot illustrating ONLY this spoken narration moment. "
            "Narration anchor: Western safety standards. Visual direction: historical photo scan. "
            "Specific context: Leningrad NPP 1975 document archival photo; Sosnovy Bor, USSR; 1975. "
            "Prefer genuine, dated archival material from an identifiable archive."
        )
        query = _search_query(prompt)
        self.assertIn("Leningrad NPP 1975", query)
        self.assertNotIn("Narration anchor", query)
        self.assertNotIn("Prefer genuine", query)

    def test_archival_query_is_bounded(self):
        query = _search_query("Specific context: " + ("historical power plant construction 1975 " * 30))
        self.assertLessEqual(len(query), 180)


if __name__ == "__main__":
    unittest.main()
