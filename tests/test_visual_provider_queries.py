"""Regression tests for concise visual-provider search queries."""
import unittest
from unittest.mock import patch

from core.images import _compact_search_query, fetch_clip, generate_image
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

    def test_image_libraries_receive_concise_scene_query_not_generation_wrapper(self):
        prompt = "Create a visual that directly illustrates this exact spoken narration. Spoken line: ..."
        query = "noise cancelling headphones sound wave interference"
        with patch("core.images._cloudflare", return_value=False), \
             patch("core.images._pixabay_image", return_value=True) as pixabay:
            self.assertTrue(generate_image(prompt, "scene.jpg", search_query=query))
        pixabay.assert_called_once_with(query, "scene.jpg")

    def test_long_scene_brief_is_compacted_to_subject_anchors(self):
        from core.pipeline import _scene_search_brief
        brief = _scene_search_brief(
            {
                "subject": "RMS Titanic lifeboat launch and evacuation of passengers",
                "location": "North Atlantic Ocean near Newfoundland",
                "time_period": "night of 14 April 1912 early twentieth century",
            },
            ["Titanic lifeboats launching", "passenger evacuation", "archival newsreel"],
        )
        tokens = brief.split()
        self.assertLessEqual(len(tokens), 12)
        self.assertIn("titanic", tokens)
        self.assertIn("lifeboat", tokens)
        self.assertIn("north", tokens)
        self.assertIn("atlantic", tokens)
        self.assertNotIn("archival", tokens)
        self.assertNotIn("twentieth", tokens)

    def test_clip_search_keeps_scene_brief_ahead_of_many_keywords(self):
        scene = "noise cancelling headphones sound wave interference"
        keywords = [f"generic keyword {index}" for index in range(10)]
        with patch("core.footage_sources.fetch_documentary_clip", return_value=False) as fetch, \
             patch("core.footage_sources._is_archival_video_request", return_value=False), \
             patch("core.images.stock_providers", return_value=[]):
            self.assertFalse(fetch_clip(keywords, "scene.mp4", set(), scene_prompt=scene))
        queries = fetch.call_args.args[0]
        self.assertEqual(queries[0], scene)
        self.assertLessEqual(len(queries), 8)
        self.assertIn("generic keyword 0", queries)


if __name__ == "__main__":
    unittest.main()
