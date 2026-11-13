"""Shared fixtures for the review-contract tests.

``make_package`` writes a schema-v2 review package.  With ``real_media=False``
the MP4 is placeholder bytes and ffprobe/ffmpeg are replaced by ``fake_media``,
so the contract logic is tested on any machine.  With ``real_media=True`` the
package holds a real H.264/AAC file rendered by ffmpeg.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from core import review_contract as contract

NOTE = "Inspected this carefully against the original source."
SIZES = {"longform": (1920, 1080), "shortform": (1080, 1920), "daily_news": (1080, 1920)}


def have_ffmpeg() -> bool:
    try:
        from core.ffmpeg_utils import ffmpeg_bin, ffprobe_bin
        return bool(shutil.which(ffmpeg_bin()) and shutil.which(ffprobe_bin()))
    except Exception:
        return False


def fake_probe(mode: str = "shortform", **overrides) -> dict:
    width, height = SIZES[mode]
    probe = {"width": width, "height": height, "duration_seconds": 12.0, "fps": 30.0,
             "video_codec": "h264", "pixel_format": "yuv420p", "audio_codec": "aac",
             "audio_channels": 2, "audio_sample_rate": 48000, "audio_stream_count": 1, "bit_rate": 4_000_000}
    probe.update(overrides)
    return probe


@contextlib.contextmanager
def fake_media(mode: str = "shortform", decoded: bool = True, **probe_overrides):
    """Replace ffprobe/ffmpeg with fixed answers (placeholder MP4 bytes cannot be probed)."""
    def probe(path):
        path = Path(path)
        if not path.is_file():
            raise contract.ContractError(f"Video file is missing: {path.name}")
        if path.stat().st_size < 1024:
            raise contract.ContractError("Video file is empty or truncated")
        return fake_probe(mode, **probe_overrides)

    def frames(video, frames_dir, count=6, duration_seconds=None):
        Path(frames_dir).mkdir(parents=True, exist_ok=True)
        (Path(frames_dir) / "frame_001.jpg").write_bytes(b"frame")
        return 1

    with patch.object(contract, "probe_video", side_effect=probe), \
            patch.object(contract, "decode_check",
                         return_value={"decoded": decoded, "detail": "decoded" if decoded else "Decode errors: x"}), \
            patch.object(contract, "extract_preview_frames", side_effect=frames):
        yield


def render_real_video(path: Path, mode: str = "shortform", seconds: int = 3) -> None:
    from core.ffmpeg_utils import ffmpeg_bin
    width, height = SIZES[mode]
    subprocess.run(
        [ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=size={width}x{height}:rate=30:duration={seconds}", "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-shortest", str(path)],
        check=True, capture_output=True, timeout=180)


def source_asset(**overrides) -> dict:
    asset = {
        "path": "assets/scene_01.jpg", "asset_type": "image",
        "provenance": {"origin": "source", "provider": "NASA", "title": "Apollo 11 launch",
                       "creator": "NASA", "source_url": "https://images.nasa.gov/details/KSC-69PC-442"},
        "rights": {"license": "Public Domain", "license_url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                   "attribution": "NASA"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(asset.get(key), dict):
            asset[key] = {**asset[key], **value}
        else:
            asset[key] = value
    return asset


def make_package(root, mode: str = "shortform", *, real_media: bool = False, assets=None, qa=None,
                 research=None, job_id: str | None = None, environ=None, scene_count: int = 2) -> dict:
    """Write a complete package under ``root`` and return its manifest."""
    root = Path(root)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "qa").mkdir(exist_ok=True)
    if real_media:
        render_real_video(root / "video.mp4", mode)
    else:
        (root / "video.mp4").write_bytes(b"\0" * 4096)
    (root / "captions.ass").write_text("[Script Info]\n[Events]\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hi\n",
                                       encoding="utf-8")
    statuses = {"technical": "PASS", "semantic": "REVIEW", "audio": "REVIEW", "captions": "REVIEW",
                "assets": "REVIEW"}
    statuses.update(qa or {})
    for name, status in statuses.items():
        if status is None:
            continue
        (root / "qa" / f"{name}.json").write_text(json.dumps({"status": status, "details": [f"{name} detail"]}),
                                                  encoding="utf-8")
    scenes = []
    for index in range(scene_count):
        asset = dict(assets[index]) if assets is not None else source_asset()
        asset["path"] = f"assets/scene_{index + 1:02d}.jpg"
        (root / asset["path"]).write_bytes(b"image-bytes-%d" % index)
        scenes.append({"scene_id": f"scene-{index + 1:02d}", "narration": f"Narration for scene {index + 1}.",
                       "start_seconds": index * 2.0, "duration_seconds": 2.0, "assets": [asset]})
    env = {"GITHUB_RUN_ID": "4242", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_SHA": "a" * 40} if environ is None else environ
    kwargs = dict(mode=mode, topic="Test topic", scenes=scenes, job_id=job_id, pipeline_job_id=7,
                  research=research, environ=env,
                  extra={"publish": {"title": "Test title", "description": "Test description",
                                     "tags": ["physics"], "category_id": "27"}})
    if real_media:
        contract.extract_preview_frames(root / "video.mp4", root / "preview" / "frames", count=2)
        manifest = contract.build_manifest(root, **kwargs)
        contract.write_package(root, manifest)
    else:
        with fake_media(mode):
            contract.extract_preview_frames(root / "video.mp4", root / "preview" / "frames")
            manifest = contract.build_manifest(root, **kwargs)
            contract.write_package(root, manifest)
    return manifest


def approve(root, manifest: dict | None = None, **changes) -> dict:
    """Decisions that approve everything in this package (as the dashboard would export them)."""
    root = Path(root)
    manifest = manifest or contract.read_manifest(root)
    statuses = contract.qa_statuses(root)
    decisions = contract.decision_template(manifest)
    decisions.update(reviewer="Reviewer A", reviewed_at=contract.now_iso())
    decisions["overall"] = {"decision": "approved", "notes": NOTE}
    decisions["confirmations"] = {name: "approved" for name in contract.CONFIRMATIONS}
    for item in decisions["scene_reviews"]:
        item.update(decision="approved", notes=NOTE)
    for item in decisions["asset_reviews"]:
        item.update(source_verified=True, rights_decision="approved", notes=NOTE)
    for item in decisions["claim_reviews"]:
        item.update(decision="verified", notes=NOTE)
    for item in decisions["source_reviews"]:
        item.update(decision="reviewed", notes=NOTE)
    for name in contract.HUMAN_RESOLVABLE_QA:
        if statuses.get(name) == "REVIEW":
            decisions["qa_reviews"][name] = {"decision": "accepted", "notes": NOTE}
    decisions.update(changes)
    return decisions


class FakeUploader:
    """Stands in for core.uploader; records calls.  Nothing leaves the machine."""

    def __init__(self, *, privacy="private", channel_id="UC_TEST", video_channel_id=None, existing=None,
                 verified=True, publish_at=None, processing="ok", search_error=None, found=True):
        self.privacy, self.channel_id, self.existing = privacy, channel_id, existing
        self.video_channel_id = video_channel_id or channel_id
        self.verified, self.publish_at, self.processing = verified, publish_at, processing
        self.search_error, self.found = search_error, found
        self.uploads = []

    def verify_channel(self, force=False):
        if self.verified == "raise":
            raise RuntimeError("Authenticated channel is not the configured channel")
        return ({"verified": True, "id": self.channel_id, "title": "Engineering Physics",
                 "handle": "@engineeringphysics-n5g"} if self.verified else {"verified": False, "skipped": True})

    def find_private_video_by_marker(self, marker):
        if self.search_error:
            raise RuntimeError(self.search_error)
        return self.existing

    def upload(self, video_path, title, description, tags, **kwargs):
        self.uploads.append({"video_path": Path(video_path), "title": title, "description": description,
                             "tags": tags, **kwargs})
        self.existing = "vid_new"
        return "vid_new"

    def wait_processed(self, video_id, max_wait=600):
        return self.processing

    def get_video_status(self, video_id):
        if not self.found:
            return {"found": False}
        return {"found": True, "privacy_status": self.privacy, "publish_at": self.publish_at,
                "upload_status": "processed", "channel_id": self.video_channel_id, "title": "Test title"}
