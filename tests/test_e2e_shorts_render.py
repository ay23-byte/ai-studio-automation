"""End-to-end Shorts build with real FFmpeg and no network.

Narration and image providers are replaced by local generators, so the test
exercises timing alignment, caption burn-in, the explainer-card fallback, the
batched join, technical QA, sidecar/manifest writing, checkpoint resume and the
release gate exactly as a production run does.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import config, ffmpeg_utils

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

SCRIPT = {
    "title": "How a transformer changes voltage", "hook": "Two coils, one field", "category": "physics",
    "scenes": [
        {"say": "A transformer has two separate coils of copper wire wrapped around one shared iron core.", "caption": "Two coils share one iron core.",
         "label": "Two coils", "image": "copper coils on an iron core", "keywords": ["transformer", "coil"]},
        {"say": "Alternating current in the first coil creates a magnetic field that keeps changing direction every cycle.", "caption": "Changing current makes a changing field.",
         "label": "Magnetic field", "image": "magnetic field lines around a coil", "keywords": ["magnetic field"]},
        {"say": "That changing field induces a voltage in the second coil, set by its number of turns.", "caption": "The field induces voltage in coil two.",
         "label": "Induction", "image": "a glowing second coil", "keywords": ["induction"]},
    ],
}


def _fake_synthesise(text, build_dir, language="english", gender="male"):
    """A 6 s tone stands in for speech; timings split it by sentence."""
    path = Path(build_dir) / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=44100:duration=6", "-af", "volume=0.5,tremolo=f=5:d=0.6",
                    "-ac", "1", str(path)], check=True)
    sentences = [part.strip() + "." for part in text.split(".") if part.strip()]
    step = 6.0 / max(len(sentences), 1)
    return path, [(i * step, (i + 1) * step, sentence) for i, sentence in enumerate(sentences)]


@unittest.skipUnless(HAVE_FFMPEG, "FFmpeg/FFprobe are required for the render test")
class ShortsEndToEndTests(unittest.TestCase):
    def setUp(self):
        from core import db
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.output, self.build = root / "output", root / "build"
        self.output.mkdir(); self.build.mkdir()
        self.image_calls = []

        def fake_image(prompt, path, category="general", **kwargs):
            """Provider answers for every scene except the second."""
            from PIL import Image, ImageDraw
            self.image_calls.append(Path(path).name)
            if Path(path).name == "img1.jpg":
                return False
            image = Image.new("RGB", (1080, 1920), (20 + 60 * len(self.image_calls), 60, 120))
            draw = ImageDraw.Draw(image)
            for offset in range(0, 1920, 120):
                draw.rectangle([100, offset, 980, offset + 60], fill=(200, 180 - offset // 20, 60))
            image.save(path, quality=90)
            Path(str(path) + ".source.json").write_text(json.dumps(
                {"provider": "test-generator", "ai_generated": True, "licence": "generated"}), encoding="utf-8")
            return True

        patches = [
            patch.object(config, "OUTPUT_DIR", self.output), patch.object(config, "BUILD_DIR", self.build),
            patch.object(config, "STATE_DIR", root / "state"), patch.object(config, "LOG_DIR", root / "logs"),
            patch.object(db, "DB_PATH", root / "studio.sqlite"),
            patch("core.pipeline.synthesise", side_effect=_fake_synthesise),
            patch("core.pipeline.generate_image", side_effect=fake_image),
            patch("core.pipeline.fetch_clip", return_value=False),
            patch("core.pipeline.generate_ai_video", return_value=False),
            patch("core.images.generate_image", return_value=False),   # thumbnail backgrounds stay offline
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "ALLOW_FALLBACK_VISUAL_UPLOAD": "",
                                    "THUMBNAIL_PROVIDER": "local", "OPENVINO_LTX_MODEL_DIR": ""}),
        ]
        self.mocks = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)
        db.init_db()
        self.db = db
        self.synth = self.mocks[5]

    def test_build_resume_and_release_gate(self):
        from core import pipeline, release_gate
        job_id = self.db.save_video(SCRIPT["title"], "shorts", "english", "physics", SCRIPT)
        with patch.object(pipeline.cfg, "OUTPUT_DIR", self.output), patch.object(pipeline.cfg, "BUILD_DIR", self.build):
            video = Path(pipeline.build_shorts(SCRIPT, job_id, language="english"))
            self.assertTrue(video.is_file())
            self.assertFalse(list(self.output.glob("*.partial*")), "partial render left beside the deliverable")

            streams = ffmpeg_utils.probe_streams(video)
            self.assertEqual((streams["width"], streams["height"]), (1080, 1920))
            self.assertEqual(streams["video_codec"], "h264")
            self.assertEqual(streams["pix_fmt"], "yuv420p")
            self.assertEqual(streams["audio_codec"], "aac")
            self.assertAlmostEqual(streams["duration_seconds"], 6.0, delta=1.5)
            self.assertEqual(streams["fps"], 30.0)

            job_dir = self.build / f"job_{job_id}"
            quality = json.loads((job_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertIn(quality["status"], {"PASS", "REVIEW"}, quality.get("issues"))
            fallback = json.loads((job_dir / "visual_fallback_report.json").read_text(encoding="utf-8"))
            self.assertEqual((fallback["fallback_card_count"], fallback["visual_count"]), (1, 3))
            self.assertFalse(quality.get("upload_blocked"))
            for name in ("captions.en.srt", "captions.en.vtt", "asset_manifest.json", "licenses.csv",
                         "credits.txt", "scene_timing.json", "checkpoint.json", "subs.ass"):
                self.assertTrue((job_dir / name).is_file(), name)
            timing = json.loads((job_dir / "scene_timing.json").read_text(encoding="utf-8"))
            spans = timing if isinstance(timing, list) else timing.get("scenes", [])
            self.assertEqual(len(spans), 3)

            row = self.db.get_video(job_id)
            self.assertEqual(row["status"], "done")
            # Every automated gate passes; the only thing a fresh build lacks is the human review.
            gate = release_gate.evaluate(row, job_dir)
            self.assertEqual([item.split(":")[0] for item in gate["blocking"]], ["human_review"], gate["blocking"])
            self.assertEqual({c["status"] for c in gate["checks"] if c["check"] != "human_review"}, {"PASS"})

            # Resume: nothing expensive is repeated and the same file is returned.
            voice_calls, image_calls = self.synth.call_count, len(self.image_calls)
            again = Path(pipeline.build_shorts(SCRIPT, job_id, language="english"))
            self.assertEqual(again, video)
            self.assertEqual(self.synth.call_count, voice_calls)
            self.assertLessEqual(len(self.image_calls) - image_calls, 1)  # only the failed scene may be retried

    def test_failed_render_is_recorded_and_resumable(self):
        from core import pipeline
        job_id = self.db.save_video("Resume " + SCRIPT["title"], "shorts", "english", "physics", SCRIPT)
        with patch.object(pipeline.cfg, "OUTPUT_DIR", self.output), patch.object(pipeline.cfg, "BUILD_DIR", self.build):
            with patch("core.editor_shorts.render", side_effect=RuntimeError("simulated encoder crash")):
                with self.assertRaises(RuntimeError):
                    pipeline.build_shorts(SCRIPT, job_id, language="english")
            self.assertEqual(self.db.get_video(job_id)["status"], "failed")
            self.assertFalse((self.output / f"shorts_{job_id}.mp4").exists())
            calls = self.synth.call_count
            video = Path(pipeline.build_shorts(SCRIPT, job_id, language="english"))
            self.assertTrue(video.is_file())
            self.assertEqual(self.synth.call_count, calls, "narration was synthesised again on resume")
            self.assertEqual(self.db.get_video(job_id)["status"], "done")


@unittest.skipUnless(HAVE_FFMPEG, "FFmpeg is not installed")
class CreditSlateQualityTests(unittest.TestCase):
    """An appended credit slate is dark and silent by design, not a defect."""

    def test_end_card_is_excluded_from_black_and_silence_totals(self):
        from core import quality
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "with_slate.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=6",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30:d=4",
                "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100:duration=6",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=4",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v];[2:a][3:a]concat=n=2:v=0:a=1[a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-t", "10", str(video)], check=True)
            whole = quality.inspect_video(video, profile="longform", sample_count=2)["metrics"]
            body = quality.inspect_video(video, profile="longform", sample_count=2, body_seconds=6.0)["metrics"]
        self.assertGreater(whole["black_seconds"], 3.0)
        self.assertGreater(whole["silence_seconds"], 3.0)
        self.assertEqual(body["black_seconds"], 0)
        self.assertEqual(body["silence_seconds"], 0)
        self.assertAlmostEqual(body["end_card_seconds"], 4.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()
