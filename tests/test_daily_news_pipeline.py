import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from core.daily_news_pipeline import collect_feed, collect_candidates, mark_seen, load_seen
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
    @patch('core.daily_news_pipeline.collect_feed')
    def test_deduplicates_same_link(self, feed):
        item = {'id':'abc','title':'Headline','url':'https://x.test/a','publisher':'X','published':''}
        feed.return_value = [item, dict(item)]
        result = collect_candidates(['https://feed.test'])
        self.assertEqual(result['candidate_count'], 1)
        self.assertIn('independently verified', result['notice'])
    def test_seen_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            mark_seen(['a','b'], tmp)
            self.assertEqual(load_seen(Path(tmp) / 'seen.json'), {'a','b'})
if __name__ == '__main__': unittest.main()