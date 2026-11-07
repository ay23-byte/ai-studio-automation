import json, tempfile, unittest
from pathlib import Path
from core.news_shorts_writer import generate_reviewed_shorts, _validate_scientific_wording
class WriterGateTests(unittest.TestCase):
 def test_unapproved_packet_is_rejected_before_api_call(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'packet.json'; p.write_text(json.dumps({'review_status':'pending_human_verification'}))
   with self.assertRaisesRegex(ValueError,'review_status'): generate_reviewed_shorts(p,Path(tmp)/'out.json')
 def test_rejects_vertical_river_height_wording(self):
  script={'scenes':[{'say':'293,800 लोग 0 से 50 मीटर वर्टिकल रिवर ऊंचाई में हैं।','caption':'0-50 m HAND'}]}
  with self.assertRaisesRegex(ValueError,'unsupported causal, exposure, or distance wording'):
   _validate_scientific_wording(script)
 def test_rejects_hand_without_nearest_drainage_definition_in_scene(self):
  script={'scenes':[
   {'say':'HAND का मतलब ऊँचाई है।','caption':'HAND = vertical height above nearest drainage'},
   {'say':'293,800 people potential exposure','caption':'293,800'}
  ]}
  with self.assertRaisesRegex(ValueError,'same scene'):
   _validate_scientific_wording(script)

from unittest.mock import patch, Mock
from core.news_shorts_writer import _post_gemini_with_retries

class GeminiRetryTests(unittest.TestCase):
 def test_retries_503_then_succeeds(self):
  first=Mock(status_code=503)
  second=Mock(status_code=200)
  second.raise_for_status.return_value=None
  with patch('core.news_shorts_writer.requests.post', side_effect=[first,second]) as post, \
       patch('core.news_shorts_writer.time.sleep') as sleep:
   result=_post_gemini_with_retries('https://example.test','key',{})
  self.assertIs(result,second)
  self.assertEqual(post.call_count,2)
  sleep.assert_called_once_with(2)

 def test_uses_three_attempts_before_failing(self):
  responses=[Mock(status_code=503) for _ in range(3)]
  with patch('core.news_shorts_writer.requests.post', side_effect=responses) as post, \
       patch('core.news_shorts_writer.time.sleep'):
   with self.assertRaisesRegex(RuntimeError,'after 3 attempts'):
    _post_gemini_with_retries('https://example.test','key',{})
  self.assertEqual(post.call_count,3)

 def test_does_not_retry_nontransient_http_error(self):
  response=Mock(status_code=400)
  from requests import HTTPError
  response.raise_for_status.side_effect=HTTPError('bad request',response=response)
  with patch('core.news_shorts_writer.requests.post',return_value=response) as post, \
       patch('core.news_shorts_writer.time.sleep') as sleep:
   with self.assertRaises(HTTPError):
    _post_gemini_with_retries('https://example.test','key',{})
  post.assert_called_once()
  sleep.assert_not_called()


if __name__=='__main__': unittest.main()