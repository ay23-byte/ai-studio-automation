"""Regression tests for the browser-based 3D presenter capture contract."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_recorder_captures_video_only_and_keeps_audio_for_lipsync():
    html = (ROOT / "avatar_studio" / "index.html").read_text(encoding="utf-8")
    assert "new MediaStream([...canvasStream.getVideoTracks()])" in html
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
    assert '"-map","0:v:0","-map","1:a:0"' in runner
    assert 'const audioDuration = Number(durationProbe.stdout.trim())' in runner
    assert "muxed MP4 duration mismatch" in runner
