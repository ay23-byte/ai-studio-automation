import json, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from scripts.qa_news_short import run

GOOD={"streams":[
 {"codec_type":"video","width":1080,"height":1920,"avg_frame_rate":"30/1","codec_name":"h264","pix_fmt":"yuv420p"},
 {"codec_type":"audio","codec_name":"aac","sample_rate":"48000"}],"format":{"duration":"45"}}
AUDIO={"status":"PASS","warnings":[],"issues":[],"metrics":{"integrated_lufs":-16.0}}
VISUAL={"status":"PASS","summary":{"measured_shot_count":3},"issues":[],"shots":[]}
FFMPEG_OK=subprocess.CompletedProcess(args=["ffmpeg"],returncode=0,stdout="",stderr="")

class QATests(unittest.TestCase):
 @patch("scripts.qa_news_short.subprocess.run")
 @patch("scripts.qa_news_short.subprocess.check_output")
 def test_rejects_bad_profile(self,probe,run_cmd):
  probe.return_value=json.dumps({"streams":[
   {"codec_type":"video","width":720,"height":1280,"avg_frame_rate":"30/1","codec_name":"h264","pix_fmt":"yuv420p"},
   {"codec_type":"audio","codec_name":"aac","sample_rate":"44100"}],"format":{"duration":"45"}})
  result=run(Path("fake.mp4"))
  self.assertEqual(result["status"],"FAIL"); self.assertFalse(result["publication_ready"])
  self.assertTrue(any("resolution" in item for item in result["errors"]))

 @patch("core.visual_consistency.audit_visual_consistency",return_value=VISUAL)
 @patch("core.audio_quality.inspect_audio",return_value=AUDIO)
 @patch("scripts.qa_news_short._sample_video_frames",return_value=["a.jpg","b.jpg","c.jpg"])
 @patch("scripts.qa_news_short.subprocess.run",return_value=FFMPEG_OK)
 @patch("scripts.qa_news_short.subprocess.check_output",return_value=json.dumps(GOOD))
 def test_pass_requires_audio_and_visual_checks(self,probe,run_cmd,frames,audio,visual):
  with tempfile.TemporaryDirectory() as temp: result=run(Path("short.mp4"),Path(temp))
  self.assertEqual(result["status"],"PASS"); self.assertFalse(result["publication_ready"])
  self.assertEqual(result["sampled_frame_count"],3); audio.assert_called_once(); visual.assert_called_once()
  self.assertEqual(run_cmd.call_count,2)

 @patch("core.visual_consistency.audit_visual_consistency",return_value={"status":"FAIL","summary":{"measured_shot_count":3},"issues":[{"level":"error","message":"underexposed"}],"shots":[]})
 @patch("core.audio_quality.inspect_audio",return_value=AUDIO)
 @patch("scripts.qa_news_short._sample_video_frames",return_value=["a.jpg","b.jpg","c.jpg"])
 @patch("scripts.qa_news_short.subprocess.run",return_value=FFMPEG_OK)
 @patch("scripts.qa_news_short.subprocess.check_output",return_value=json.dumps(GOOD))
 def test_visual_failure_blocks_qa(self,probe,run_cmd,frames,audio,visual):
  with tempfile.TemporaryDirectory() as temp: result=run(Path("short.mp4"),Path(temp))
  self.assertEqual(result["status"],"FAIL"); self.assertTrue(any("Visual quality" in x for x in result["errors"]))

 @patch("core.visual_consistency.audit_visual_consistency",return_value=VISUAL)
 @patch("core.audio_quality.inspect_audio",return_value={"status":"FAIL","warnings":[],"issues":["clipping"]})
 @patch("scripts.qa_news_short._sample_video_frames",return_value=["a.jpg","b.jpg","c.jpg"])
 @patch("scripts.qa_news_short.subprocess.run",return_value=FFMPEG_OK)
 @patch("scripts.qa_news_short.subprocess.check_output",return_value=json.dumps(GOOD))
 def test_audio_failure_blocks_qa(self,probe,run_cmd,frames,audio,visual):
  with tempfile.TemporaryDirectory() as temp: result=run(Path("short.mp4"),Path(temp))
  self.assertEqual(result["status"],"FAIL"); self.assertTrue(any("Audio quality" in x for x in result["errors"]))

 @patch("core.visual_consistency.audit_visual_consistency",return_value=VISUAL)
 @patch("core.audio_quality.inspect_audio",return_value=AUDIO)
 @patch("scripts.qa_news_short._sample_video_frames",return_value=[])
 @patch("scripts.qa_news_short.subprocess.run",return_value=FFMPEG_OK)
 @patch("scripts.qa_news_short.subprocess.check_output",return_value=json.dumps(GOOD))
 def test_missing_visual_samples_blocks_qa(self,probe,run_cmd,frames,audio,visual):
  with tempfile.TemporaryDirectory() as temp: result=run(Path("short.mp4"),Path(temp))
  self.assertEqual(result["status"],"FAIL"); self.assertTrue(any("could not inspect" in x for x in result["errors"]))

if __name__=="__main__": unittest.main()
