"""Verify a review render contains the media timeline recorded by the renderer."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(manifest_path):
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    video = Path(data["video_path"])
    if not video.is_file():
        raise FileNotFoundError(f"Rendered MP4 missing: {video}")
    visual = data.get("visual_renderer") or {}
    segments = visual.get("segments") or []
    if visual.get("renderer") != "ffmpeg_media_asset_timeline" or not segments:
        raise ValueError("Manifest does not prove an asset-backed FFmpeg timeline was rendered.")
    for item in segments:
        source = Path(item["asset_path"])
        if not source.is_file():
            raise FileNotFoundError(f"Source asset missing after render: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != item.get("source_sha256"):
            raise ValueError(f"Source asset hash mismatch for shot {item.get('shot')}")
        if item.get("renderer") != "ffmpeg_media_asset":
            raise ValueError(f"Unexpected renderer for shot {item.get('shot')}")
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height,r_frame_rate:format=duration",
        "-of", "json", str(video)
    ], text=True)
    info = json.loads(probe)
    stream = next((x for x in info.get("streams", []) if x.get("codec_type") == "video"), None)
    if not stream or int(stream.get("width", 0)) != 1080 or int(stream.get("height", 0)) != 1920:
        raise ValueError("Review MP4 is not 1080x1920.")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0 or len(segments) != int(visual.get("actual_assets_rendered") or 0):
        raise ValueError("Rendered duration or segment count is invalid.")
    return {"video": str(video), "duration_seconds": duration,
            "resolution": "1080x1920", "rendered_source_segments": len(segments),
            "source_hashes_verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    args = parser.parse_args()
    result = verify(args.manifest)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
