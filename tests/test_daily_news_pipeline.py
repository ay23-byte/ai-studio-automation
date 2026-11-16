import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from core.daily_news_pipeline import collect_feed, collect_candidates, mark_seen, load_seen, _plain
RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><item><title>Example headline</title><link>https://example.com/story</link><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate><source>Example News</source></item></channel></rss>'''
class DailyNewsPipelineTests(unittest.TestCase):
    @patch('core.daily_news_pipeline.requests.get')
    def test_collects_metadata_only(self, get):
        class Response:
            content = RSS
            def raise_for_status(self): pass
        get.return_value = Response()
        rows = collect_feed('https://feed.example/rss')
        self.assertEqual(rows[0]['title'], 'Example headline')
        self.assertEqual(rows[0]['publisher'], 'Example News')
        get.assert_called_once()
    def test_plain_omits_styled_publisher_credit_but_keeps_story_text(self):
        description = '<a>Hindu community leaders discuss the issue.</a><font color="#6f6f6f">The Hindu</font>'
        self.assertEqual(_plain(description), 'Hindu community leaders discuss the issue.')

    @patch('core.daily_news_pipeline.collect_feed')
    def test_deduplicates_same_link(self, feed):
        item = {'id':'abc','title':'Headline','url':'https://x.test/a','publisher':'X','published':''}
        feed.return_value = [item, dict(item)]
        result = collect_candidates(['https://feed.test'])
        self.assertEqual(result['candidate_count'], 1)
        self.assertIn('independently verified', result['notice'])
    @patch('core.daily_news_pipeline.collect_feed')
    def test_reports_feed_health_and_partial_failures(self, feed):
        item = {'id':'abc','title':'Headline','url':'https://x.test/a','publisher':'X','published':''}
        def response(url):
            if url.endswith('/broken'):
                raise RuntimeError('temporary feed outage')
            return [item]
        feed.side_effect = response
        result = collect_candidates(['https://feed.test/ok', 'https://feed.test/broken'])
        self.assertEqual(result['feed_count'], 2)
        self.assertEqual(result['healthy_feed_count'], 1)
        self.assertEqual(result['failed_feed_count'], 1)
        self.assertEqual(result['feed_health'][0]['candidate_count'], 1)
        self.assertEqual(result['feed_health'][1]['status'], 'error')
        self.assertEqual(result['candidate_count'], 1)

    def test_seen_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark_seen(['a','b'], tmp)
            self.assertEqual(load_seen(Path(tmp) / 'seen.json'), {'a','b'})
if __name__ == '__main__': unittest.main()