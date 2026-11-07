import unittest

from core.images import _image_style, _relevance
from core.footage_sources import _expanded_queries, _metadata_relevance
from core.workflow_audit import audit_media_assets


class VisualRelevanceTests(unittest.TestCase):
    def test_pixabay_metadata_relevance(self):
        self.assertGreater(_relevance("Battle of Plassey Bengal 1757", "Battle of Plassey in Bengal"), 0.5)
        self.assertEqual(_relevance("Battle of Plassey Bengal", "sunset beach ocean"), 0.0)

    def test_archive_metadata_relevance(self):
        score = _metadata_relevance("Jallianwala Bagh Amritsar 1919", {
            "title": "Jallianwala Bagh massacre in Amritsar",
            "description": "Historical event in 1919"
        })
        self.assertGreater(score, 0.5)

    def test_india_query_expansion_is_contextual(self):
        queries = _expanded_queries(["Battle of Plassey"], "East India Company in Bengal")
        self.assertIn("Battle of Plassey India", queries)
        generic = _expanded_queries(["deep space nebula"], "astronomy")
        self.assertNotIn("deep space nebula India", generic)

    def test_category_styles_are_distinct(self):
        self.assertIn("historically grounded", _image_style("history").lower())
        self.assertIn("laboratory", _image_style("science").lower())
        self.assertNotEqual(_image_style("history"), _image_style("finance"))

    def test_audit_warns_when_visual_has_not_been_reviewed(self):
        report = audit_media_assets([{
            "scene_number": 1, "provider": "Wikimedia Commons",
            "page_url": "https://commons.wikimedia.org/wiki/File:Example",
            "licence": "CC BY-SA", "licence_reviewed": True,
            "relevance_score": 0.8, "path": None
        }], [1])
        stages = [issue["stage"] for issue in report["issues"]]
        self.assertIn("visual_authenticity", stages)
        self.assertFalse(report["publication_ready"])


if __name__ == "__main__":
    unittest.main()
