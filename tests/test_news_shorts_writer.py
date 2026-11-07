import json, tempfile, unittest
from pathlib import Path
from core.news_shorts_writer import generate_reviewed_shorts, _validate_scientific_wording, _build_correction_prompt, _normalize_spoken_hand_definitions
class WriterGateTests(unittest.TestCase):
 def test_unapproved_packet_is_rejected_before_api_call(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'packet.json'; p.write_text(json.dumps({'review_status':'pending_human_verification'}))
   with self.assertRaisesRegex(ValueError,'review_status'): generate_reviewed_shorts(p,Path(tmp)/'out.json')
 def test_rejects_50_metre_radius_wording(self):
  script={'scenes':[{'say':'लोग 50 मीटर दायरे में रहते हैं।','caption':'50 मीटर दायरे'}]}
  with self.assertRaisesRegex(ValueError,'unsupported causal, exposure, or distance wording'):
   _validate_scientific_wording(script)
 def test_rejects_vertical_river_height_wording(self):
  script={'scenes':[{'say':'293,800 लोग 0 से 50 मीटर वर्टिकल रिवर ऊंचाई में हैं।','caption':'0-50 m HAND'}]}
  with self.assertRaisesRegex(ValueError,'unsupported causal, exposure, or distance wording'):
   _validate_scientific_wording(script)
 def test_requires_nepal_hand_ranges_in_spoken_narration(self):
  script={'scenes':[{'say':'HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई। 293,800 लोग संभावित exposure में हैं। नेपाल में 108,500 और 22,800 लोग हैं। 46% भवन क्षेत्रफल और 77% भवन क्षेत्रफल बढ़ा। यह संभावित जोखिम है, बाढ़ का पूर्वानुमान नहीं। अध्ययन यह स्थापित नहीं करता कि निर्माण ने बाढ़ कराई।','caption':'108,500: 0–50 m HAND; 22,800: 0–10 m HAND'}]}
  with self.assertRaisesRegex(ValueError,'108,500 as the Nepal 0–50 m HAND estimate'):
   _validate_scientific_wording(script)

 def test_rejects_claim_that_construction_had_no_effect(self):
  script={'scenes':[{'say':'HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई। 293,800 लोग संभावित exposure में हैं। 108,500 लोग 0–50 m HAND में और 22,800 लोग 0–10 m HAND में हैं। 46% भवन क्षेत्रफल और 77% भवन क्षेत्रफल बढ़ा। यह संभावित जोखिम है, बाढ़ का पूर्वानुमान नहीं। अध्ययन के अनुसार निर्माण ने बाढ़ की संभावना नहीं बढ़ाई।','caption':'building footprint area'}]}
  with self.assertRaisesRegex(ValueError,'Caveat overstates the evidence'):
   _validate_scientific_wording(script)

 def test_normalizer_adds_definition_to_each_spoken_hand_scene(self):
  script={'scenes':[
   {'say':'नेपाल में 108,500 लोग 0–50 मीटर HAND सीमा में हैं।','caption':'Nepal estimate'},
   {'say':'22,800 लोग 0–10 मीटर HAND सीमा में हैं।','caption':'Nepal estimate'},
   {'say':'293,800 लोग संभावित exposure में हैं।','caption':'Potential exposure'}
  ]}
  normalized = _normalize_spoken_hand_definitions(script)
  definition = 'HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई'
  self.assertIn(definition, normalized['scenes'][0]['say'])
  self.assertIn(definition, normalized['scenes'][1]['say'])
  self.assertNotIn('HAND', normalized['scenes'][2]['say'])

 def test_normalizer_is_idempotent(self):
  script={'scenes':[{'say':'HAND यानी निकटतम जलधारा से ऊपर की ऊर्ध्वाधर ऊँचाई; 108,500 लोग 0–50 मीटर HAND सीमा में हैं।','caption':''}]}
  once = _normalize_spoken_hand_definitions(script)['scenes'][0]['say']
  twice = _normalize_spoken_hand_definitions(script)['scenes'][0]['say']
  self.assertEqual(once, twice)

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


class ScriptCorrectionPromptTests(unittest.TestCase):
 def test_overlong_script_gets_specific_compression_instruction(self):
  prompt = _build_correction_prompt("BASE", ValueError("Script word count outside safe target: 151."))
  self.assertIn("151 spoken words", prompt)
  self.assertIn("hard maximum is 140", prompt)
  self.assertIn("target 85–100", prompt)
  self.assertIn("Do not weaken scientific guardrails", prompt)
  self.assertIn("Nepal ranges", prompt)
  self.assertIn("building footprint area", prompt)
  self.assertIn("at most 22 whitespace-separated words", prompt)
  self.assertIn("total MUST be 70–140", prompt)

 def test_short_script_gets_supported_expansion_instruction(self):
  prompt = _build_correction_prompt("BASE", ValueError("Script word count outside safe target: 55."))
  self.assertIn("minimum is 70", prompt)
  self.assertIn("supported by the reviewed packet", prompt)
  self.assertNotIn("hard maximum is 140", prompt)

 def test_non_word_count_failure_keeps_scientific_correction(self):
  prompt = _build_correction_prompt("BASE", ValueError("HAND missing definition"))
  self.assertIn("निकटतम जलधारा", prompt)
  self.assertIn("Do not weaken scientific guardrails", prompt)
  self.assertNotIn("WORD-COUNT REPAIR", prompt)
  self.assertIn("same say field MUST contain the complete phrase", prompt)
  self.assertIn("Do not put the definition only in a caption or another scene", prompt)
  self.assertIn("108,500 लोग 0–50 मीटर", prompt)


if __name__=='__main__': unittest.main()
 
class GeminiFallbackModelTests(unittest.TestCase):
 def test_primary_then_two_ordered_fallbacks(self):
  from core.news_shorts_writer import _gemini_model_candidates
  self.assertEqual(
   _gemini_model_candidates('gemini-3.8-flash'),
   ['gemini-3.8-flash','gemini-3.6-flash','gemini-3.5-flash-lite'])

 def test_deduplicates_configured_fallback_model(self):
  from core.news_shorts_writer import _gemini_model_candidates
  self.assertEqual(
   _gemini_model_candidates('gemini-3.6-flash'),
   ['gemini-3.6-flash','gemini-3.5-flash-lite'])

 def test_uses_lite_as_primary_without_duplicate_fallbacks(self):
  from core.news_shorts_writer import _gemini_model_candidates
  self.assertEqual(
   _gemini_model_candidates('gemini-3.5-flash-lite'),
   ['gemini-3.5-flash-lite','gemini-3.6-flash'])
