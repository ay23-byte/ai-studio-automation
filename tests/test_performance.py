import unittest
from core.performance import rows_as_dicts, summarize_video_performance, build_content_recommendations


class PerformanceTests(unittest.TestCase):
    def test_rows_map_headers_to_values(self):
        rows = rows_as_dicts([["v1", 20]], [{"name": "video"}, {"name": "views"}])
        self.assertEqual(rows[0], {"video": "v1", "views": 20})

    def test_summary_calculates_engagement_per_view(self):
        result = summarize_video_performance([
            {"video": "a", "title": "A", "views": 100, "likes": 10, "comments": 2, "shares": 3, "averageViewDuration": 30},
            {"video": "b", "title": "B", "views": 50, "likes": 5, "comments": 1, "shares": 1, "averageViewDuration": 20},
        ])
        self.assertAlmostEqual(result["videos"][0]["engagement_per_view_pct"], 15.0)
        self.assertEqual(result["videos"][0]["title"], "A")
        self.assertTrue(result["insights"])

    def test_empty_data_is_handled(self):
        result = summarize_video_performance([])
        self.assertEqual(result["videos"], [])
        self.assertTrue(build_content_recommendations(result))


if __name__ == "__main__":
    unittest.main()
