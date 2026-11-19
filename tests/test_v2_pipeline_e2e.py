"""Milestone 5/6/7/9: one complete offline render for each content pipeline."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.production_v2.cli import DEMO_DOSSIERS
from src.production_v2.config import V2Config
from src.production_v2.ffmpeg_io import probe, resolve_ffmpeg
from src.production_v2.models import QualityReport
from src.production_v2.pipeline import dossier_from_dict, run_pipeline


class PipelineE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = V2Config.from_env({})
        cls.ffmpeg = resolve_ffmpeg(cls.config)
        if not cls.ffmpeg:
            raise unittest.SkipTest("FFmpeg is not available; real render tests cannot run here")

    def test_all_three_pipelines_render_and_fail_closed_on_demo_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.update({"PV2_OUTPUT_DIR": tmp, "PV2_DEMO_MODE": "1", "PV2_UPLOAD": "0"})
            config = V2Config.from_env(env)
            for content_type in ("documentary", "news_short", "physics_short"):
                with self.subTest(content_type=content_type):
                    data = DEMO_DOSSIERS[content_type]
                    result = run_pipeline(content_type, data["topic"],
                                          dossier_from_dict(data), config)
                    self.assertTrue(result["video_ready"], result.get("error"))
                    # Demo sources are explicitly not publication evidence.
                    self.assertEqual(result["state"], "QUALITY_REJECTED")
                    root = Path(result["artifact_dir"])
                    video = root / "final.mp4"
                    self.assertTrue(video.is_file() and video.stat().st_size > 50_000)
                    media = probe(video, config)
                    streams = media.get("streams", [])
                    self.assertTrue(any(s.get("codec_type") == "video" for s in streams))
                    self.assertTrue(any(s.get("codec_type") == "audio" for s in streams))
                    rules = config.rules_for(content_type)
                    video_stream = next(s for s in streams if s.get("codec_type") == "video")
                    self.assertEqual((video_stream["width"], video_stream["height"]),
                                     (rules.width, rules.height))
                    decision = json.loads((root / "quality_decision.json").read_text(encoding="utf-8"))
                    self.assertEqual(set(decision), set(QualityReport.FIELD_NAMES))
                    self.assertEqual(decision["upload_eligibility"], "FAIL")
                    for required in ("subs.srt", "subs.ass", "licenses.csv", "credits.txt",
                                     "script.json", "research_assessment.json",
                                     "artifact_manifest.json", "run_report.json"):
                        self.assertTrue((root / required).is_file(), f"{content_type}: {required} missing")


if __name__ == "__main__":
    unittest.main()
