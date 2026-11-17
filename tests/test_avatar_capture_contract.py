"""Regression tests for the browser-based 3D presenter capture contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_recorder_captures_video_only_and_keeps_audio_for_lipsync():
    html = (ROOT / "avatar_studio" / "index.html").read_text(encoding="utf-8")
    assert "\n      const videoOnlyStream=new MediaStream([...canvasStream.getVideoTracks()]);" in html
    assert "new MediaRecorder(videoOnlyStream" in html
    assert "new MediaRecorder(combined" not in html
    capture_setup = html.split("const videoOnlyStream", 1)[1].split("recorder=new MediaRecorder", 1)[0]
    assert "sourceStream.getAudioTracks()" not in capture_setup
    assert "await avatar.attachStream(sourceStream)" in html


def test_runner_requires_video_only_webm_and_muxes_original_narration():
    runner = (ROOT / "avatar_studio" / "generate-clips.mjs").read_text(encoding="utf-8")
    assert 'webmStreams.some(stream=>stream.codec_type==="video")' in runner
    assert 'webmStreams.some(stream=>stream.codec_type==="audio")' in runner
    assert '||webmStreams.some(stream=>stream.codec_type==="audio")' in runner
    assert 'Recording avatar video while narration drives lip-sync' in runner
    # Production runner must wait for the exact completion status emitted by index.html.
    assert 'Saved video-only talking_presenter.webm' in runner
    assert 'Saved talking_presenter.webm' not in runner
    assert 'const captureTimeout = Math.max(60_000, Math.ceil((audioDuration + 30) * 1000))' in runner
    assert '"-map","0:v:0","-map","1:a:0"' in runner
    assert 'const audioDuration = Number(durationProbe.stdout.trim())' in runner
    assert "muxed MP4 duration mismatch" in runner


def test_browser_smoke_test_expects_video_only_recording():
    smoke = (ROOT / "avatar_studio" / "ci-smoke-test.mjs").read_text(encoding="utf-8")
    assert 'Recording avatar video while narration drives lip-sync' in smoke
    assert 'Saved video-only talking_presenter.webm' in smoke
    assert 'result.audioTracks !== 0' in smoke
    assert 'result.audioTracks < 1' not in smoke



def test_documentary_production_uses_frame_accurate_offline_renderer():
    generation = (ROOT / "core" / "avatar_clip_generation.py").read_text(encoding="utf-8")
    renderer = (ROOT / "avatar_studio" / "render-offline.mjs").read_text(encoding="utf-8")
    assert 'studio / "render-offline.mjs"' in generation
    assert "generate-clips.mjs" not in generation
    # The old real-time MediaRecorder path could export a 50 ms clip for an
    # eight-second narration. Production now renders exactly one frame per
    # narration frame and checks both decoded frame count and final duration.
    assert "Math.round(seconds * fps)" in renderer
    assert "Math.abs(decoded - frames) > 1" in renderer
    assert "Math.abs(duration - seconds) > 0.25" in renderer
    assert '"-i", audio' in renderer
    assert '"offline-frame-accurate"' in renderer
