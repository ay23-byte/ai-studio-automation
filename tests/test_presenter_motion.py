"""The presenter motion check, run on real encoded clips, and the offline renderer contract."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core import avatar_clip_generation
from core.avatar_clip_generation import PresenterRenderError, generate_chapter_avatar_clips
from core.ffmpeg_utils import ffmpeg_bin, ffprobe_bin
from core.presenter_quality import analyse_presenter_motion, read_render_record
from tests.footage_fixture import have_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
STUDIO = ROOT / "avatar_studio"
needs_ffmpeg = unittest.skipUnless(have_ffmpeg(), "FFmpeg/FFprobe are required to build and measure real clips")

FPS, WIDTH, HEIGHT, SECONDS = 30, 320, 180, 4
MOUTH_BOX = {"x": 0.40, "y": 0.50, "width": 0.20, "height": 0.16}
# Narration is audible in these frame ranges and silent between them.
VOICED = [(6, 40), (52, 84), (94, 116)]


def _voiced(frame):
    return any(start <= frame < end for start, end in VOICED)


def _mouth_track(frames):
    """Mouth opening per frame: changes while narration is audible, closed in the pauses."""
    track = []
    for frame in range(frames):
        track.append(round(0.5 + 0.5 * np.sin(frame * 1.9), 3) if _voiced(frame) else 0.0)
    return track


def _picture(frame, mouth_open, head_moves=True):
    """A textured 'face' that drifts like a head, with a mouth that opens by `mouth_open`."""
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    shift = 3.0 * np.sin(frame * 0.21) if head_moves else 0.0
    image = 110 + 60 * np.sin((xs + shift) * 0.11) * np.cos((ys + shift * 0.6) * 0.09)
    left, top = int(MOUTH_BOX["x"] * WIDTH), int(MOUTH_BOX["y"] * HEIGHT)
    right, bottom = left + int(MOUTH_BOX["width"] * WIDTH), top + int(MOUTH_BOX["height"] * HEIGHT)
    centre = (top + bottom) // 2
    half = int(1 + mouth_open * (bottom - top) * 0.4)
    image[centre - half:centre + half, left + 6:right - 6] = 20
    return np.clip(image, 0, 255).astype(np.uint8)


def _write_clip(path, pictures):
    """Encode the pictures at 30 fps with a tone during the voiced frames."""
    samples = np.zeros(int(16000 * len(pictures) / FPS), dtype=np.int16)
    for start, end in VOICED:
        a, b = int(start / FPS * 16000), min(len(samples), int(end / FPS * 16000))
        samples[a:b] = (9000 * np.sin(np.arange(b - a) * 2 * np.pi * 180 / 16000)).astype(np.int16)
    audio = path.with_suffix(".s16")
    audio.write_bytes(samples.tobytes())
    encode = subprocess.run([
        ffmpeg_bin(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{WIDTH}x{HEIGHT}",
        "-framerate", str(FPS), "-i", "-", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", str(audio),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ], input=b"".join(picture.tobytes() for picture in pictures), capture_output=True, timeout=120)
    if encode.returncode != 0:
        raise RuntimeError(encode.stderr.decode("utf-8", "replace"))
    return path


@needs_ffmpeg
class PresenterMotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frames = FPS * SECONDS
        self.track = _mouth_track(self.frames)

    def _analyse(self, pictures, **options):
        clip = _write_clip(self.root / "clip.mp4", pictures)
        options.setdefault("mouth_box", MOUTH_BOX)
        return analyse_presenter_motion(clip, ffmpeg=ffmpeg_bin(), ffprobe=ffprobe_bin(), **options)

    def test_talking_head_with_moving_mouth_passes(self):
        report = self._analyse([_picture(i, self.track[i]) for i in range(self.frames)], mouth_track=self.track)
        self.assertEqual(report["status"], "PASS", report)
        self.assertEqual(report["frames_analysed"], self.frames)
        self.assertGreaterEqual(report["distinct_frame_rate"], 25)
        self.assertEqual(report["mouth_track_offset_frames"], 0)
        self.assertGreater(report["voiced_mouth_motion_ratio"], 0.8)
        self.assertEqual(report["human_visual_review"], "pending")

    def test_repeated_pictures_are_not_counted_as_frames(self):
        # Ten pictures a second, each shown three times: the file still says 30 fps.
        pictures = [_picture(i - i % 3, self.track[i - i % 3]) for i in range(self.frames)]
        report = self._analyse(pictures)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["nominal_frame_rate"], 30.0)
        self.assertLess(report["distinct_frame_rate"], 13)
        self.assertTrue(any("different frames" in issue for issue in report["issues"]), report["issues"])

    def test_frozen_second_fails(self):
        pictures = [_picture(min(i, 45) if i < 80 else i, self.track[min(i, 45) if i < 80 else i]) for i in range(self.frames)]
        report = self._analyse(pictures)
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["longest_frozen_seconds"], 0.9)
        self.assertTrue(any("frozen" in issue for issue in report["issues"]), report["issues"])

    def test_a_slow_pause_between_sentences_is_not_a_freeze(self):
        # Production run 43652866814 rejected a clip for a "0.53 s freeze" that was the presenter
        # pausing between two sentences: the head still moved, a fraction of a pixel per frame.
        def drifting(frame):
            ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
            shift = 0.1 * frame                                      # slow, continuous head movement
            image = 110 + 60 * np.sin((xs + shift) * 0.11) * np.cos((ys + shift * 0.6) * 0.09)
            return np.clip(image, 0, 255).astype(np.uint8)

        pictures = [_picture(i, self.track[i]) for i in range(40)] + [drifting(i) for i in range(40)] \
            + [_picture(i, self.track[i]) for i in range(80, self.frames)]
        report = self._analyse(pictures)
        self.assertLess(report["longest_frozen_seconds"], 0.5, report)
        self.assertFalse(any("frozen" in issue for issue in report["issues"]), report["issues"])
        self.assertGreaterEqual(report["distinct_frame_rate"], 15)

    def test_head_that_moves_with_a_still_mouth_fails(self):
        report = self._analyse([_picture(i, 0.0) for i in range(self.frames)])
        self.assertEqual(report["status"], "FAIL")
        self.assertGreaterEqual(report["distinct_frame_rate"], 25)   # the picture itself is moving
        self.assertLess(report["voiced_mouth_motion_ratio"], 0.5)
        self.assertTrue(any("mouth moves in only" in issue for issue in report["issues"]), report["issues"])

    def test_picture_out_of_step_with_the_rendered_mouth_track_fails(self):
        late = 6
        pictures = [_picture(max(0, i - late), self.track[max(0, i - late)]) for i in range(self.frames)]
        report = self._analyse(pictures, mouth_track=self.track)
        self.assertEqual(report["mouth_track_offset_frames"], late)
        self.assertTrue(any("6 frames behind" in issue for issue in report["issues"]), report["issues"])

    def test_without_a_mouth_position_the_clip_is_never_passed(self):
        report = self._analyse([_picture(i, self.track[i]) for i in range(self.frames)], mouth_box=None)
        self.assertEqual(report["status"], "REVIEW")
        self.assertFalse(report["mouth_measured"])
        self.assertTrue(any("not measured" in warning for warning in report["warnings"]))

    def test_unreadable_file_raises(self):
        broken = self.root / "broken.mp4"
        broken.write_bytes(b"this is not a video")
        with self.assertRaises(RuntimeError):
            analyse_presenter_motion(broken, ffmpeg=ffmpeg_bin(), ffprobe=ffprobe_bin())


class PresenterRenderFailureTests(unittest.TestCase):
    """A failed render must say why, not only that a command exited with 1."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "avatar_studio" / "node_modules" / "three").mkdir(parents=True)
        (self.root / "avatar_studio" / "node_modules" / "three" / "package.json").write_text("{}")
        patcher = patch.object(avatar_clip_generation, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sections = [{"_start": 0.0, "_dur": 60.0}]

    def _run(self, node_result=None, node_error=None):
        def fake_run(command, **options):
            self.assertIn("timeout", options)
            if command[0] == "node":
                if node_error:
                    raise node_error
                return node_result
            if "-show_entries" in command:
                return subprocess.CompletedProcess(command, 0, stdout="8.0\n", stderr="")
            Path(command[-1]).write_bytes(b"\0" * 4096)   # the narration slice
            return subprocess.CompletedProcess(command, 0, stdout="", stderr=b"")
        with patch.object(avatar_clip_generation.subprocess, "run", side_effect=fake_run):
            return generate_chapter_avatar_clips(self.root / "voice.wav", self.sections, "female", self.root / "build")

    def test_renderer_message_reaches_the_error(self):
        result = subprocess.CompletedProcess(["node"], 1, stdout="Presenter chapter 1/3 done\n",
                                             stderr="::error title=Presenter render::The lip-sync model found no speech sounds in chapter 2.\n")
        with self.assertRaises(PresenterRenderError) as caught:
            self._run(result)
        self.assertIn("no speech sounds in chapter 2", str(caught.exception))
        self.assertIn("exit 1", str(caught.exception))

    def test_timeout_is_reported_as_a_presenter_failure(self):
        with self.assertRaises(PresenterRenderError) as caught:
            self._run(node_error=subprocess.TimeoutExpired(["node"], 10, output="Presenter chapter 1/7: 240 frames", stderr=""))
        self.assertIn("did not finish", str(caught.exception))
        self.assertIn("chapter 1/7", str(caught.exception))

    def test_missing_node_is_reported(self):
        with self.assertRaises(PresenterRenderError) as caught:
            self._run(node_error=FileNotFoundError("node"))
        self.assertIn("Node.js is not installed", str(caught.exception))

    def test_missing_browser_packages_are_reported_before_rendering(self):
        (self.root / "avatar_studio" / "node_modules" / "three" / "package.json").unlink()
        with patch.dict("os.environ", {"AVATAR_NODE_MODULES": ""}):
            with self.assertRaises(PresenterRenderError) as caught:
                self._run(subprocess.CompletedProcess(["node"], 0, stdout="", stderr=""))
        self.assertIn("npm ci", str(caught.exception))

    def test_a_render_that_produces_no_clip_fails(self):
        with self.assertRaises(PresenterRenderError) as caught:
            self._run(subprocess.CompletedProcess(["node"], 0, stdout="", stderr=""))
        self.assertIn("did not produce chapter 1", str(caught.exception))


class OfflineRendererContractTests(unittest.TestCase):
    """Properties of the renderer that the production failures showed to matter."""

    def setUp(self):
        self.page = (STUDIO / "offline.html").read_text(encoding="utf-8")
        self.driver = (STUDIO / "render-offline.mjs").read_text(encoding="utf-8")

    def test_nothing_is_loaded_from_a_cdn_while_rendering(self):
        for text in (self.page, self.driver):
            for host in ("jsdelivr", "unpkg.com", "esm.sh", "cdnjs", "https://"):
                self.assertNotIn(host, text)

    def test_frames_come_from_animation_frame_callbacks_not_timers_or_a_recorder(self):
        self.assertIn("requestAnimationFrame", self.page)
        self.assertNotIn("MediaRecorder", self.page + self.driver)
        self.assertNotIn("captureStream", self.page + self.driver)
        self.assertNotIn("setInterval", self.page + self.driver)

    def test_frame_rate_is_constant_and_frames_are_counted_in_the_file(self):
        self.assertIn("-count_frames", self.driver)
        self.assertIn("[24, 25, 30].includes(fps)", self.driver)
        self.assertIn("were rendered", self.driver)

    def test_browser_packages_are_pinned_to_exact_versions(self):
        package = json.loads((STUDIO / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((STUDIO / "package-lock.json").read_text(encoding="utf-8"))
        for name, version in package["dependencies"].items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", name)
            self.assertEqual(lock["packages"][f"node_modules/{name}"]["version"], version)
            self.assertTrue(lock["packages"][f"node_modules/{name}"]["integrity"].startswith("sha512-"))

    def test_render_record_reader_tolerates_a_missing_or_broken_file(self):
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / "presenter_section_01.mp4"
            self.assertEqual(read_render_record(clip), {})
            clip.with_suffix(".render.json").write_text("{not json")
            self.assertEqual(read_render_record(clip), {})
            clip.with_suffix(".render.json").write_text(json.dumps({"mouth_box": MOUTH_BOX}))
            self.assertEqual(read_render_record(clip)["mouth_box"], MOUTH_BOX)


if __name__ == "__main__":
    unittest.main()
