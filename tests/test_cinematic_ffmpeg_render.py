"""End-to-end FFmpeg smoke tests for the real Shorts and documentary editors.

These tests use generated local fixtures only. They make no network/API calls and
do not represent visual approval of real production footage.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.editor_shorts import render as render_shorts
from core.editor_longform import render as render_longform


def run(command):
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class CinematicFFmpegRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / "fixture.png"
        self.clip = self.root / "fixture.mp4"
        self.voice = self.root / "voice.wav"
        self.ass = self.root / "captions.ass"
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "testsrc2=size=640x360:rate=30", "-frames:v", "1", str(self.image)])
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "testsrc2=size=640x360:rate=30", "-t", "2", "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.clip)])
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=44100", "-t", "3",
             "-c:a", "pcm_s16le", str(self.voice)])
        self.ass.write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
            "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: Default,Arial,32,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,2,0,2,20,20,20,1\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,Render smoke test\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def assert_mp4(self, path, expected_width, expected_height):
        self.assertTrue(path.is_file(), f"Missing output: {path}")
        self.assertGreater(path.stat().st_size, 50_000)
        result = subprocess.check_output([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)
        ], text=True).strip()
        self.assertEqual(result, f"{expected_width},{expected_height}")

    def test_shorts_editor_renders_image_and_broll_with_camera_motion(self):
        out = self.root / "shorts.mp4"
        media = [
            {"type": "image", "path": str(self.image),
             "cinema_settings": {"movement": "slow_push_in"}},
            {"type": "video", "path": str(self.clip),
             "cinema_settings": {"movement": "tracking"}},
        ]
        render_shorts(media, [1.0, 1.0], [1.0], self.voice, self.ass,
                      2.0, out, music=None, sfx_vol=0)
        self.assert_mp4(out, 1080, 1920)

    def test_documentary_editor_renders_image_and_broll_with_camera_motion(self):
        out = self.root / "documentary.mp4"
        sections = [
            {"_dur": 1.0, "_media": {"type": "image", "path": str(self.image)},
             "cinema_settings": {"movement": "slow_pull_out"}},
            {"_dur": 1.0, "_media": {"type": "video", "path": str(self.clip)},
             "cinema_settings": {"movement": "pan"}},
        ]
        render_longform(sections, self.voice, self.ass, 2.0, out,
                        music=None, credits_path=None)
        self.assert_mp4(out, 1920, 1080)


if __name__ == "__main__":
    unittest.main()
