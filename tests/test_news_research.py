import unittest
from unittest.mock import Mock, patch

from core import news_research


class NewsResearchRetryTests(unittest.TestCase):
    @patch("core.news_research.time.sleep")
    @patch("core.news_research.requests.get")
    def test_gdelt_rate_limit_retries_and_recovers(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "0"})
        ok = Mock(status_code=200, headers={})
        ok.json.return_value = {"articles": [{
            "title": "A verified news lead",
            "url": "https://example.org/story",
            "domain": "example.org",
            "seendate": "20261115T000000Z",
        }]}
        get.side_effect = [limited, ok]

        result = news_research.research_topic("documentary topic")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["publisher"], "example.org")
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(0.0)

    @patch("core.news_research.time.sleep")
    @patch("core.news_research.requests.get")
    def test_gdelt_persistent_rate_limit_fails_softly_after_bounded_attempts(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "0"})
        get.return_value = limited

        result = news_research.research_topic("documentary topic")

        self.assertEqual(result, [])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
