import unittest
from unittest.mock import patch
from pathlib import Path
from scripts.qa_news_short import run
class QATests(unittest.TestCase):
 @patch("scripts.qa_news_short.subprocess.run")
 @patch("scripts.qa_news_short.subprocess.check_output")
 def test_rejects_bad_profile(self,probe,run_cmd):
  probe.return_value='{"streams":[{"codec_type":"video","width":720,"height":1280,"avg_frame_rate":"30/1","codec_name":"h264","pix_fmt":"yuv420p"},{"codec_type":"audio","codec_name":"aac","sample_rate":"44100"}],"format":{"duration":"45"}}'
  run_cmd.return_value.stderr=""
  r=run(Path("fake.mp4")); self.assertEqual(r["status"],"FAIL"); self.assertFalse(r["publication_ready"])
