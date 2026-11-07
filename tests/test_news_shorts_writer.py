import json, tempfile, unittest
from pathlib import Path
from core.news_shorts_writer import generate_reviewed_shorts
class WriterGateTests(unittest.TestCase):
 def test_unapproved_packet_is_rejected_before_api_call(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'packet.json'; p.write_text(json.dumps({'review_status':'pending_human_verification'}))
   with self.assertRaisesRegex(ValueError,'review_status'): generate_reviewed_shorts(p,Path(tmp)/'out.json')
if __name__=='__main__': unittest.main()