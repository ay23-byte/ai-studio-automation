"""Assemble a fail-closed shared review package for a rendered standard Short."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_review_package(script: dict, build_dir: str | Path, render_dir: str | Path) -> Path:
    build = Path(build_dir)
    render = Path(render_dir)
    root = render / "shared_review_package"
    assets_dir = root / "assets"
    frames_dir = root / "preview" / "frames"
    qa_dir = root / "qa"
    assets_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)

    video = render / "video.mp4"
    captions = render / "captions.ass"
    if not video.is_file() or not captions.is_file():
        raise FileNotFoundError("Review render must contain video.mp4 and captions.ass.")

    scenes = []
    for index, scene in enumerate(script.get("scenes") or []):
        candidates = [build / f"img{index}.jpg", build / f"raw{index}.mp4"]
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            raise FileNotFoundError(f"Rendered visual for scene {index + 1} is missing.")
        relative = Path("assets") / f"scene_{index + 1:02d}{source.suffix.lower()}"
        target = root / relative
        shutil.copy2(source, target)
        sidecar = source.with_suffix(source.suffix + ".source.json")
        metadata = {}
        if sidecar.is_file():
            try:
                metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}

        provider = str(metadata.get("provider") or "")
        source_url = str(metadata.get("page_url") or metadata.get("source_url") or "")
        is_ai = "ai_generated" in str(metadata.get("asset_type") or "").lower() or provider.lower() == "pollinations"
        if is_ai:
            origin = "ai_generated"
            provenance = {"origin": origin, "provider": provider or "unknown",
                          "prompt": str(scene.get("image") or scene.get("say") or "Prompt not retained by renderer")}
        elif provider and source_url:
            origin = "source"
            provenance = {"origin": origin, "provider": provider, "source_url": source_url}
        else:
            origin = "unknown"
            provenance = {"origin": origin, "provider": provider or None,
                          "source_url": source_url or None}
        license_name = metadata.get("licence") or metadata.get("license")
        scenes.append({
            "scene_id": f"scene-{index + 1}",
            "narration": str(scene.get("say") or ""),
            "assets": [{
                "path": relative.as_posix(),
                "asset_type": "video" if source.suffix.lower() == ".mp4" else "image",
                "provenance": provenance,
                "rights": {"status": "unknown", "license": license_name or None,
                           "attribution": metadata.get("attribution_text")},
            }],
        })

    shutil.copy2(video, root / "video.mp4")
    shutil.copy2(captions, root / "captions.ass")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True, check=False, timeout=120,
    )
    technical_status = "FAIL"
    technical_detail = "ffprobe could not verify the rendered MP4."
    if probe.returncode == 0:
        try:
            data = json.loads(probe.stdout)
            streams = data.get("streams", [])
            videos = [item for item in streams if item.get("codec_type") == "video"]
            audios = [item for item in streams if item.get("codec_type") == "audio"]
            if len(videos) == 1 and audios and videos[0].get("width") == 1080 and videos[0].get("height") == 1920:
                technical_status = "PASS"
                technical_detail = "One 1080x1920 video stream and at least one audio stream verified."
            else:
                technical_detail = "Expected one 1080x1920 video stream and at least one audio stream."
        except (ValueError, TypeError):
            technical_detail = "ffprobe returned invalid JSON."
    for name, status, detail in (
        ("technical", technical_status, technical_detail),
        ("semantic", "REVIEW", "Human must verify that every visual accurately matches its narration."),
        ("audio", "REVIEW", "Human must review narration, mix, loudness, clipping, and intelligibility."),
        ("captions", "REVIEW", "Human must review caption wording, timing, spelling, and synchronization."),
        ("assets", "REVIEW", "Human must verify source provenance, licence terms, attribution, and usage rights."),
    ):
        _write_json(qa_dir / f"{name}.json", {"status": status, "details": [detail]})

    checklist = {field: "pending" for field in
                 ("editorial", "factual", "visual", "audio", "rights", "disclosure")}
    checklist["human_review_status"] = "pending"
    _write_json(root / "review_checklist.json", checklist)
    (root / "review_checklist.md").write_text(
        "# Human review required\n\n"
        "- [ ] Editorial quality and structure\n- [ ] Factual accuracy and evidence\n"
        "- [ ] Visual relevance and authenticity\n- [ ] Narration and audio mix\n"
        "- [ ] Captions and synchronization\n- [ ] Asset provenance, licences, and attribution\n"
        "- [ ] AI disclosure and platform policy\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1, "mode": "shortform",
        "topic": str(script.get("title") or "Untitled Short"),
        "video_file": "video.mp4", "captions_file": "captions.ass",
        "scenes": scenes,
    }
    _write_json(root / "production_manifest.json", manifest)

    preview = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(video),
         "-frames:v", "1", str(frames_dir / "frame_001.jpg")],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if preview.returncode != 0 or not (frames_dir / "frame_001.jpg").is_file():
        raise RuntimeError("Could not extract a preview frame for human review.")
    summary = subprocess.run(
        ["python", "scripts/validate_review_package.py", str(root),
         "--output", str(root / "package_summary.json")],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if summary.returncode not in (0, 1):
        raise RuntimeError("Shared review-package validator could not complete: " + summary.stderr[-1000:])
    result = json.loads((root / "package_summary.json").read_text(encoding="utf-8"))
    if result.get("status") not in {"REVIEW_REQUIRED", "APPROVED_FOR_UPLOAD"}:
        raise RuntimeError("Shared review-package validation failed: " + "; ".join(result.get("errors", [])))
    if result.get("status") != "REVIEW_REQUIRED":
        raise RuntimeError("A new render must remain REVIEW_REQUIRED until a human completes the checklist.")
    return root
