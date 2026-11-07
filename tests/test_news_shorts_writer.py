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
if __name__=='__main__': unittest.main()