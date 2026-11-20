"""Opt-in real renderer integration smoke tests; never use mocks.

Run PV2_TEST_REAL_ANIMATIONS=1 python -m pytest tests/test_v2_real_animation.py.
Both tools and FFmpeg must already be installed; absence is a failure in this mode.
"""
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from src.production_v2.artifacts import ArtifactStore
from src.production_v2.config import V2Config
from src.production_v2.editor import Editor
from src.production_v2.ffmpeg_io import probe, resolve_ffmpeg
from src.production_v2.licenses import AssetLibrary
from src.production_v2.models import NarrationSegment, Scene, VisualSpec
from src.production_v2.visuals import plan_visuals


@pytest.mark.skipif(os.environ.get("PV2_TEST_REAL_ANIMATIONS") != "1", reason="real animation tools are opt-in")
@pytest.mark.parametrize("kind", ["manim_wave", "blender_orbit"])
def test_real_animation_render_and_probe(kind):
    import json
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"test_scene": {"type": kind, "reviewed": True}}))
        config = V2Config.from_env({**os.environ, "PV2_VISUAL_MANIFEST": str(manifest)})
        config.rules["physics_short"] = replace(config.rules["physics_short"], width=320, height=240, fps=5)
        assert resolve_ffmpeg(config), "real integration test requires FFmpeg"
        scene = Scene("test_scene", "test", VisualSpec("diagram", "animation", duration=2))
        assets = plan_visuals([scene], root, AssetLibrary(), config, "physics_short")
        assert assets[0].image_path.stat().st_size > 1000
        media = probe(assets[0].image_path, config)
        assert any(s.get("codec_type") == "video" and s.get("width") == 320 and s.get("height") == 240
                   for s in media["streams"])

        # Exercise the real editor with the produced clip, audio, and subtitle stream.
        import wave
        wav = root / "speech.wav"
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 32000)
        srt = root / "captions.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,500\nTest caption.\n", encoding="utf-8")
        store = ArtifactStore(root, "editing")
        segment = NarrationSegment("test_scene", "Test caption.", 0, 2, str(wav), "test")
        edited = Editor().render(scenes=[scene], assets=assets, segments=[segment], mixed_audio=wav,
                                 subtitles=srt, store=store, config=config, content_type="physics_short")
        final = probe(Path(edited["video"]), config)
        types = {stream.get("codec_type") for stream in final["streams"]}
        assert {"video", "audio", "subtitle"}.issubset(types)
        assert not edited["warnings"]
