import unittest
from core.seo import estimate_chapters, normalize_seo_package


class SeoTests(unittest.TestCase):
    def test_shorts_have_no_chapters(self):
        self.assertEqual(estimate_chapters({"format": "shorts", "sections": [{"title": "A"}]}), [])

    def test_longform_chapters_start_at_zero_and_increase(self):
        script = {
            "format": "longform", "estimated_minutes": 6,
            "sections": [
                {"title": "Intro", "narration": "one two three four"},
                {"title": "Second", "narration": "five six seven eight"},
                {"title": "Third", "narration": "nine ten"},
            ],
        }
        chapters = estimate_chapters(script)
        self.assertEqual(chapters[0]["timestamp"], "00:00")
        self.assertLess(chapters[1]["timestamp"], chapters[2]["timestamp"])

    def test_metadata_limits_and_monotonic_chapters(self):
        package = normalize_seo_package({
            "titles": ["x" * 120], "tags": [f"tag{i}" for i in range(40)],
            "hashtags": ["#one", "#two", "#three", "#four"],
            "chapters": [
                {"timestamp": "00:00", "title": "Intro"},
                {"timestamp": "00:00", "title": "Duplicate"},
                {"timestamp": "00:45", "title": "Next"},
                {"timestamp": "bad", "title": "Invalid"},
            ],
        })
        self.assertEqual(len(package["titles"][0]), 100)
        self.assertLessEqual(len(package["tags"]), 30)
        self.assertLessEqual(len(package["hashtags"]), 3)
        self.assertEqual([c["timestamp"] for c in package["chapters"]], ["00:00", "00:45"])


if __name__ == "__main__":
    unittest.main()
