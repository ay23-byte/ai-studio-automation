"""Opt-in real renderer integration smoke tests; never use mocks.

Run PV2_TEST_REAL_ANIMATIONS=1 python -m pytest tests/test_v2_real_animation.py.
Both tools and FFmpeg must already be installed; absence is a failure in this mode.
"""
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from src.production_v2.config import V2Config
from src.production_v2.ffmpeg_io import probe, resolve_ffmpeg
from src.production_v2.licenses import AssetLibrary
from src.production_v2.models import Scene, VisualSpec
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
        assets = plan_visuals([Scene("test_scene", "test", VisualSpec("diagram", "animation"))],
                              root, AssetLibrary(), config, "physics_short")
        assert assets[0].image_path.stat().st_size > 1000
        media = probe(assets[0].image_path, config)
        assert any(s.get("codec_type") == "video" and s.get("width") == 320 and s.get("height") == 240
                   for s in media["streams"])
