"""Shared review-package contract for documentaries, Shorts and daily-news Shorts.

One versioned job manifest, one human-decision format and one release
evaluation are used by all three production modes:

    build workflow  ->  review package (manifest v2 + interactive dashboard)
    human reviewer  ->  review decisions (exported from the dashboard)
    release workflow -> evaluate_release() -> PRIVATE upload of that exact file

Rules enforced here (none of them can be switched off by configuration):

* every approval is bound to the job id, the production mode, the manifest
  fingerprint and the SHA-256 of the exact MP4; a changed video invalidates it;
* nothing is inferred from a missing field: absent means "not approved";
* a human can resolve an automated ``REVIEW`` with written notes, but can never
  turn a ``FAIL``, a missing report, or missing rights evidence into approval;
* editorial, visual, source, rights, attribution and technical checks are
  reported separately so one cannot stand in for another.

This module performs no network access and never uploads anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

MANIFEST_SCHEMA_VERSION = 2
DECISIONS_SCHEMA_VERSION = 2
RELEASE_REPORT_SCHEMA_VERSION = 1

MODES = {
    "longform": {"label": "Documentary", "aspect": 16 / 9, "min_width": 1280, "min_height": 720},
    "shortform": {"label": "Standard Short", "aspect": 9 / 16, "min_width": 1080, "min_height": 1920},
    "daily_news": {"label": "Daily-news Short", "aspect": 9 / 16, "min_width": 1080, "min_height": 1920},
}
ASPECT_TOLERANCE = 0.02

#: Human confirmations for the video as a whole.  Each is a separate decision.
CONFIRMATIONS = ("editorial", "factual", "visual", "audio", "rights", "disclosure")
#: Automated reports whose ``REVIEW`` status a human may resolve with notes.
HUMAN_RESOLVABLE_QA = ("semantic", "audio", "captions", "assets")
#: Automated reports that must be ``PASS``; no human note can replace them.
TECHNICAL_QA = ("technical",)
QA_FILES = {name: f"qa/{name}.json" for name in TECHNICAL_QA + HUMAN_RESOLVABLE_QA}

ORIGINS = ("source", "ai_generated", "project_graphic", "user_provided", "unknown")
MIN_NOTE_CHARS = 20
CLOCK_SKEW = timedelta(minutes=10)

MANIFEST_NAME = "production_manifest.json"
DECISIONS_NAME = "review_decisions.json"
TEMPLATE_NAME = "review_decisions_template.json"
RELEASE_REPORT_NAME = "release_report.json"


class ContractError(ValueError):
    """The package or the decisions do not satisfy the shared contract."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value) -> datetime | None:
    """Timezone-aware datetime, or ``None`` when absent, malformed or naive."""
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def sha256_file(path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_json(path, payload) -> None:
    """Atomic JSON write (a crashed run never leaves half a decision file)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_json(path, *, what: str = "file") -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"Missing {what}: {Path(path).name}") from exc
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContractError(f"Cannot read valid JSON from {what} {Path(path).name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{what} {Path(path).name} must contain a JSON object.")
    return value


def inside(root, relative) -> Path:
    """Resolve ``relative`` and refuse anything outside the package directory."""
    root = Path(root).resolve()
    text = str(relative or "").strip()
    if not text:
        raise ContractError("An empty path is not a valid package file.")
    if Path(text).is_absolute():
        raise ContractError(f"Package paths must be relative, not absolute: {text}")
    candidate = (root / text).resolve()
    if candidate != root and root not in candidate.parents:
        raise ContractError(f"Package path escapes the package directory: {text}")
    return candidate


def is_http_url(value) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


_SEARCH_PATH_PARTS = {"search", "results", "find"}
_SEARCH_QUERY_KEYS = {"q", "query", "search", "search_query", "keywords", "text", "term"}


def looks_like_search_url(value) -> bool:
    """A search-results page identifies no particular item and proves no licence."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    parts = {part.lower() for part in parsed.path.split("/") if part}
    if parts & _SEARCH_PATH_PARTS:
        return True
    return bool(set(parse_qs(parsed.query)) & _SEARCH_QUERY_KEYS)


def make_job_id(mode: str, pipeline_job_id=None, environ=None) -> str:
    """Stable job identifier that is unique across runners.

    Database ids restart at 1 on every fresh runner, so the workflow run (and
    its attempt) is part of the identity.  Outside Actions a UTC timestamp is
    used instead.
    """
    env = os.environ if environ is None else environ
    run_id = str(env.get("GITHUB_RUN_ID") or "").strip()
    suffix = f"j{pipeline_job_id}" if pipeline_job_id not in (None, "") else "j0"
    if run_id.isdigit():
        attempt = str(env.get("GITHUB_RUN_ATTEMPT") or "1").strip() or "1"
        return f"{mode}-run{run_id}-a{attempt}-{suffix}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{mode}-local{stamp}-{suffix}"


def source_context(environ=None) -> dict:
    """Where the package was built.  Values are identifiers, never secrets."""
    env = os.environ if environ is None else environ
    return {
        "workflow_run_id": str(env.get("GITHUB_RUN_ID") or "") or None,
        "workflow_run_attempt": str(env.get("GITHUB_RUN_ATTEMPT") or "") or None,
        "workflow": env.get("GITHUB_WORKFLOW") or None,
        "repository": env.get("GITHUB_REPOSITORY") or None,
        "commit": (env.get("GITHUB_SHA") or "")[:40] or None,
        "ref": env.get("GITHUB_REF_NAME") or None,
    }


# ---------------------------------------------------------------------------
# Media verification
# ---------------------------------------------------------------------------
def _tools():
    from core.ffmpeg_utils import ffmpeg_bin, ffprobe_bin
    return ffmpeg_bin(), ffprobe_bin()


def _rate(value) -> float:
    try:
        text = str(value or "0")
        if "/" in text:
            top, bottom = text.split("/", 1)
            return float(top) / float(bottom) if float(bottom) else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_video(path) -> dict:
    """Stream facts from ffprobe; raises :class:`ContractError` when unusable."""
    path = Path(path)
    if not path.is_file():
        raise ContractError(f"Video file is missing: {path.name}")
    if path.stat().st_size < 1024:
        raise ContractError(f"Video file is empty or truncated ({path.stat().st_size} bytes): {path.name}")
    _, ffprobe = _tools()
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=False, timeout=120)
    except FileNotFoundError as exc:
        raise ContractError("ffprobe is not installed; the video cannot be verified.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContractError("ffprobe timed out; the video cannot be verified.") from exc
    if result.returncode:
        raise ContractError("ffprobe could not read the MP4: " + (result.stderr or "").strip()[-300:])
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ContractError("ffprobe returned invalid JSON.") from exc
    streams = data.get("streams") or []
    videos = [item for item in streams if item.get("codec_type") == "video"
              and not (item.get("disposition") or {}).get("attached_pic")]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if len(videos) != 1:
        raise ContractError(f"Expected exactly one video stream, found {len(videos)}.")
    if not audios:
        raise ContractError("The MP4 has no audio stream.")
    video, audio = videos[0], audios[0]
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ContractError("The video stream has invalid dimensions.")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise ContractError("The MP4 duration is missing or zero.")
    return {
        "width": width, "height": height, "duration_seconds": round(duration, 3),
        "fps": round(_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
        "video_codec": video.get("codec_name"), "pixel_format": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name"),
        "audio_channels": int(audio.get("channels") or 0),
        "audio_sample_rate": int(audio.get("sample_rate") or 0),
        "audio_stream_count": len(audios),
        "bit_rate": int((data.get("format") or {}).get("bit_rate") or 0),
    }


def decode_check(path, duration_seconds: float | None = None) -> dict:
    """Decode every frame and sample.  A file that probes is not yet playable.

    Returns ``{"decoded": bool, "detail": str}``; never raises for a bad file.
    """
    ffmpeg, _ = _tools()
    budget = int(min(1800, max(120, (duration_seconds or 60) * 2)))
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(path),
             "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True, text=True, check=False, timeout=budget)
    except FileNotFoundError:
        return {"decoded": False, "detail": "ffmpeg is not installed; decoding was not verified."}
    except subprocess.TimeoutExpired:
        return {"decoded": False, "detail": f"Decoding did not finish within {budget} seconds."}
    errors = (result.stderr or "").strip()
    if result.returncode or errors:
        return {"decoded": False, "detail": "Decode errors: " + errors[-400:] if errors
                else f"ffmpeg exited with {result.returncode} while decoding."}
    return {"decoded": True, "detail": "Every video frame and audio sample decoded without error."}


def profile_problems(mode: str, probe: dict) -> list[str]:
    """Delivery-profile violations for a production mode."""
    spec = MODES.get(mode)
    if not spec:
        return [f"Unknown production mode: {mode!r}."]
    problems = []
    width, height = probe.get("width") or 0, probe.get("height") or 0
    actual = width / height if height else 0
    if abs(actual - spec["aspect"]) / spec["aspect"] > ASPECT_TOLERANCE:
        problems.append(f"Wrong aspect ratio for {mode}: {width}x{height}.")
    if width < spec["min_width"] or height < spec["min_height"]:
        problems.append(f"Resolution {width}x{height} is below the {mode} minimum "
                        f"{spec['min_width']}x{spec['min_height']}.")
    if probe.get("video_codec") != "h264":
        problems.append(f"Video codec is {probe.get('video_codec')!r}; H.264 is required.")
    if probe.get("audio_codec") != "aac":
        problems.append(f"Audio codec is {probe.get('audio_codec')!r}; AAC is required.")
    return problems


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def manifest_fingerprint(manifest: dict) -> str:
    """Hash of everything a reviewer's decision depends on.

    Review states and upload status are deliberately excluded so recording an
    approval does not invalidate it.
    """
    video = manifest.get("video") if isinstance(manifest.get("video"), dict) else {}
    research = manifest.get("research") if isinstance(manifest.get("research"), dict) else {}
    identity = {
        "schema_version": manifest.get("schema_version"),
        "job_id": manifest.get("job_id"),
        "mode": manifest.get("mode"),
        "video_sha256": video.get("sha256"),
        "scenes": [
            {"scene_id": scene.get("scene_id"), "narration": scene.get("narration"),
             "assets": [{"asset_id": asset.get("asset_id"), "path": asset.get("path"),
                         "sha256": asset.get("sha256"),
                         "origin": (asset.get("provenance") or {}).get("origin"),
                         "source_url": (asset.get("provenance") or {}).get("source_url"),
                         "license": (asset.get("rights") or {}).get("license")}
                        for asset in (scene.get("assets") or []) if isinstance(asset, dict)]}
            for scene in (manifest.get("scenes") or []) if isinstance(scene, dict)],
        "claims": [item.get("claim_id") for item in (research.get("claims") or []) if isinstance(item, dict)],
        "sources": [item.get("source_id") for item in (research.get("sources") or []) if isinstance(item, dict)],
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_manifest(root, *, mode: str, topic: str, scenes: list[dict], video_file: str = "video.mp4",
                   captions_file: str = "captions.ass", job_id: str | None = None,
                   pipeline_job_id=None, title: str | None = None, research: dict | None = None,
                   narration: dict | None = None, script: dict | None = None,
                   thumbnail_file: str | None = None, captions_language: str = "en",
                   extra: dict | None = None, environ=None) -> dict:
    """Assemble a schema-v2 manifest for the package at ``root``.

    The video and every asset are hashed here, so the manifest describes the
    files that are actually in the package.  All paths are package-relative.
    """
    root = Path(root).resolve()
    if mode not in MODES:
        raise ContractError(f"mode must be one of {', '.join(MODES)}; got {mode!r}.")
    video_path = inside(root, video_file)
    probe = probe_video(video_path)
    clean_scenes = []
    seen = set()
    for index, scene in enumerate(scenes, 1):
        scene_id = str(scene.get("scene_id") or f"scene-{index:02d}").strip()
        if scene_id in seen:
            raise ContractError(f"Duplicate scene_id in manifest: {scene_id}")
        seen.add(scene_id)
        assets = []
        for number, asset in enumerate(scene.get("assets") or [], 1):
            path = inside(root, asset.get("path"))
            if not path.is_file():
                raise ContractError(f"Asset file for {scene_id} is missing from the package: {asset.get('path')}")
            provenance = dict(asset.get("provenance") or {})
            if provenance.get("origin") not in ORIGINS:
                provenance["origin"] = "unknown"
            rights = dict(asset.get("rights") or {})
            rights["status"] = "unreviewed"  # a build never pre-approves rights
            assets.append({**{key: value for key, value in asset.items()
                              if key not in {"provenance", "rights", "path", "asset_id", "sha256"}},
                           "asset_id": str(asset.get("asset_id") or f"{scene_id}:a{number}"),
                           "path": Path(str(asset["path"])).as_posix(),
                           "sha256": sha256_file(path), "bytes": path.stat().st_size,
                           "asset_type": str(asset.get("asset_type") or "unknown"),
                           "provenance": provenance, "rights": rights})
        clean_scenes.append({**{key: value for key, value in scene.items() if key != "assets"},
                             "scene_id": scene_id,
                             "narration": str(scene.get("narration") or ""),
                             "assets": assets})
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "job_id": job_id or make_job_id(mode, pipeline_job_id, environ),
        "mode": mode,
        "topic": str(topic or "").strip(),
        "title": str(title or topic or "").strip(),
        "created_at": now_iso(),
        "source": {**source_context(environ), "pipeline_job_id": pipeline_job_id},
        "video": {"file": Path(video_file).as_posix(), "sha256": sha256_file(video_path),
                  "bytes": video_path.stat().st_size, **probe},
        # v1 readers look for these two keys; keep them in step with the above.
        "video_file": Path(video_file).as_posix(),
        "captions_file": Path(captions_file).as_posix(),
        "captions": {"file": Path(captions_file).as_posix(), "language": captions_language},
        "thumbnail_file": Path(thumbnail_file).as_posix() if thumbnail_file else None,
        "script": script or {},
        "narration": narration or {},
        "scenes": clean_scenes,
        "research": research or {"claims": [], "sources": []},
        "qa": dict(QA_FILES),
        "review": {"editorial": "pending", "visual": "pending", "source": "pending",
                   "rights": "pending", "attribution": "pending", "technical": "see qa/technical.json",
                   "reviewer": None, "decision_notes": None,
                   "release_authorization": "not_authorized"},
        "upload": {"status": "not_uploaded", "video_id": None, "privacy_status": None},
    }
    if extra:
        for key, value in extra.items():
            manifest.setdefault(key, value)
    manifest["fingerprint"] = manifest_fingerprint(manifest)
    return manifest


def read_manifest(root) -> dict:
    """Load a package manifest.  Version 1 is readable but can never be released."""
    manifest = read_json(Path(root) / MANIFEST_NAME, what="manifest")
    version = manifest.get("schema_version")
    if version not in (1, MANIFEST_SCHEMA_VERSION):
        raise ContractError(f"Unsupported manifest schema_version {version!r}; "
                            f"this studio reads versions 1 and {MANIFEST_SCHEMA_VERSION}.")
    return manifest


def manifest_structure_problems(manifest: dict) -> list[str]:
    """Structural defects of a v2 manifest (independent of the files on disk)."""
    problems = []
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return [f"The package uses manifest schema {manifest.get('schema_version')!r}. Approvals for it "
                "cannot be tied to a job id and video hash; rebuild it with the current build workflow."]
    if not str(manifest.get("job_id") or "").strip():
        problems.append("The manifest has no job_id.")
    if manifest.get("mode") not in MODES:
        problems.append("The manifest mode must be longform, shortform or daily_news.")
    if not str(manifest.get("topic") or "").strip():
        problems.append("The manifest has no topic.")
    video = manifest.get("video")
    if not isinstance(video, dict) or len(str(video.get("sha256") or "")) != 64:
        problems.append("The manifest does not record the video SHA-256.")
    scenes = manifest.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        problems.append("The manifest contains no scenes.")
        scenes = []
    seen_scenes, seen_assets = set(), set()
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            problems.append(f"Scene {index} is not an object.")
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id or scene_id in seen_scenes:
            problems.append(f"Scene {index} has a missing or duplicate scene_id.")
        seen_scenes.add(scene_id)
        if not str(scene.get("narration") or "").strip():
            problems.append(f"{scene_id or f'Scene {index}'} has no narration.")
        assets = scene.get("assets")
        if not isinstance(assets, list) or not assets:
            problems.append(f"{scene_id or f'Scene {index}'} has no asset records.")
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                problems.append(f"{scene_id}: an asset record is not an object.")
                continue
            asset_id = str(asset.get("asset_id") or "").strip()
            if not asset_id or asset_id in seen_assets:
                problems.append(f"{scene_id}: missing or duplicate asset_id.")
            seen_assets.add(asset_id)
            if (asset.get("provenance") or {}).get("origin") not in ORIGINS:
                problems.append(f"{asset_id or scene_id}: provenance origin is missing or invalid.")
    if manifest.get("fingerprint") != manifest_fingerprint(manifest):
        problems.append("The manifest fingerprint does not match its contents; the manifest was edited "
                        "after the package was built.")
    return problems


# ---------------------------------------------------------------------------
# Rights evidence (what the build recorded; a reviewer cannot supply it)
# ---------------------------------------------------------------------------
def rights_evidence_problems(asset: dict) -> list[str]:
    """Why this asset's recorded source/licence evidence is insufficient."""
    provenance = asset.get("provenance") if isinstance(asset.get("provenance"), dict) else {}
    rights = asset.get("rights") if isinstance(asset.get("rights"), dict) else {}
    origin = provenance.get("origin")
    label = str(asset.get("asset_id") or asset.get("path") or "asset")
    problems = []
    if origin == "source":
        url = provenance.get("source_url")
        if not is_http_url(url):
            problems.append(f"{label}: no original source page URL was recorded.")
        elif looks_like_search_url(url):
            problems.append(f"{label}: the recorded source URL is a search-results page, which identifies "
                            "no item and proves no licence.")
        if not str(provenance.get("provider") or "").strip():
            problems.append(f"{label}: the provider was not recorded.")
        licence = str(rights.get("license") or rights.get("licence") or "").strip()
        if not licence:
            problems.append(f"{label}: no licence was recorded for this source asset. A reviewer's entry "
                            "cannot replace missing rights evidence; replace the asset.")
        else:
            from core.asset_licensing import licence_is_usable
            usable, reason = licence_is_usable({**rights, "licence": licence,
                                                "provider": provenance.get("provider")})
            if not usable:
                problems.append(f"{label}: licence {licence!r} is unclear or not accepted ({reason}); "
                                "the asset stays blocked.")
    elif origin == "ai_generated":
        if not str(provenance.get("prompt") or "").strip():
            problems.append(f"{label}: the generation prompt of this AI asset was not recorded.")
        if not str(provenance.get("provider") or "").strip():
            problems.append(f"{label}: the generator of this AI asset was not recorded.")
    elif origin == "project_graphic":
        if not str(provenance.get("provider") or "").strip():
            problems.append(f"{label}: the renderer of this project graphic was not recorded.")
    elif origin == "user_provided":
        if not str(rights.get("license") or rights.get("licence") or "").strip():
            problems.append(f"{label}: a user-provided asset needs a recorded licence or permission basis.")
    else:
        problems.append(f"{label}: origin is unknown. Unknown provenance cannot be approved; replace the "
                        "asset or establish its origin in the build.")
    return problems


# ---------------------------------------------------------------------------
# QA reports
# ---------------------------------------------------------------------------
def qa_statuses(root) -> dict:
    """Status of each QA report: PASS, REVIEW, FAIL, NOT_RUN or INVALID."""
    statuses = {}
    for name, relative in QA_FILES.items():
        path = Path(root) / relative
        if not path.is_file():
            statuses[name] = "NOT_RUN"
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            statuses[name] = str(report.get("status") or "NOT_RUN").upper() if isinstance(report, dict) else "INVALID"
        except (OSError, UnicodeError, ValueError):
            statuses[name] = "INVALID"
    return statuses


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
def decision_template(manifest: dict) -> dict:
    """Empty decision file for this exact package.  Everything starts pending."""
    research = manifest.get("research") if isinstance(manifest.get("research"), dict) else {}
    video = manifest.get("video") if isinstance(manifest.get("video"), dict) else {}
    scenes = [scene for scene in (manifest.get("scenes") or []) if isinstance(scene, dict)]
    return {
        "schema_version": DECISIONS_SCHEMA_VERSION,
        "job_id": manifest.get("job_id"),
        "mode": manifest.get("mode"),
        "video_sha256": video.get("sha256"),
        "manifest_fingerprint": manifest.get("fingerprint"),
        "reviewer": "",
        "reviewed_at": "",
        "overall": {"decision": "pending", "notes": ""},
        "confirmations": {name: "pending" for name in CONFIRMATIONS},
        "scene_reviews": [{"scene_id": scene.get("scene_id"), "decision": "pending", "notes": ""}
                          for scene in scenes],
        "asset_reviews": [{"scene_id": scene.get("scene_id"), "asset_id": asset.get("asset_id"),
                           "source_verified": False, "rights_decision": "pending",
                           "license_url": "", "attribution": "", "notes": ""}
                          for scene in scenes for asset in (scene.get("assets") or [])
                          if isinstance(asset, dict)],
        "claim_reviews": [{"claim_id": item.get("claim_id"), "decision": "pending", "notes": ""}
                          for item in (research.get("claims") or []) if isinstance(item, dict)],
        "source_reviews": [{"source_id": item.get("source_id"), "decision": "pending", "notes": ""}
                           for item in (research.get("sources") or []) if isinstance(item, dict)],
        "qa_reviews": {name: {"decision": "pending", "notes": ""} for name in HUMAN_RESOLVABLE_QA},
        "history": [],
    }


def _notes_ok(value) -> bool:
    return isinstance(value, str) and len(value.strip()) >= MIN_NOTE_CHARS


def _index(items, key) -> tuple[dict, list[str]]:
    table, duplicates = {}, []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get(key) or "")
        if identifier in table:
            duplicates.append(identifier)
        table[identifier] = item
    return table, duplicates


def decision_problems(manifest: dict, decisions: Any, statuses: dict, *, now: datetime | None = None) -> dict:
    """Every reason these decisions do not authorise release, grouped by dimension.

    Returns ``{"identity": [...], "editorial": [...], "visual": [...], "source": [...],
    "rights": [...], "attribution": [...], "research": [...], "qa": [...]}``.
    Empty lists everywhere means the human decisions are complete and bound to
    this package.  Technical QA and rights *evidence* are checked elsewhere.
    """
    groups = {name: [] for name in ("identity", "editorial", "visual", "source", "rights",
                                    "attribution", "research", "qa")}
    if not isinstance(decisions, dict):
        groups["identity"].append("The review decisions are not a JSON object.")
        return groups
    if decisions.get("schema_version") != DECISIONS_SCHEMA_VERSION:
        groups["identity"].append(
            f"Review decisions must use schema_version {DECISIONS_SCHEMA_VERSION} (exported from the "
            "review dashboard of this package). Older decision files are not tied to a video hash.")
        return groups
    video = manifest.get("video") if isinstance(manifest.get("video"), dict) else {}
    for key, expected, label in (
            ("job_id", manifest.get("job_id"), "job"),
            ("mode", manifest.get("mode"), "production mode"),
            ("video_sha256", video.get("sha256"), "video"),
            ("manifest_fingerprint", manifest.get("fingerprint"), "manifest")):
        actual = decisions.get(key)
        if not actual:
            groups["identity"].append(f"The decisions do not state which {label} they approve ({key} is missing).")
        elif str(actual).lower() != str(expected or "").lower():
            groups["identity"].append(
                f"Stale approval: the decisions were made for a different {label} "
                f"({key} {str(actual)[:20]}… does not match this package).")

    reviewer = decisions.get("reviewer")
    if not isinstance(reviewer, str) or len(reviewer.strip()) < 2:
        groups["editorial"].append("A named human reviewer is required.")
    reviewed_at = parse_time(decisions.get("reviewed_at"))
    current = now or datetime.now(timezone.utc)
    if reviewed_at is None:
        groups["editorial"].append("reviewed_at must be an ISO-8601 time with a timezone.")
    else:
        if reviewed_at > current + CLOCK_SKEW:
            groups["editorial"].append("reviewed_at is in the future.")
        created = parse_time(manifest.get("created_at"))
        if created and reviewed_at < created - CLOCK_SKEW:
            groups["identity"].append("Stale approval: the review is dated before this package was built.")

    overall = decisions.get("overall") if isinstance(decisions.get("overall"), dict) else {}
    if overall.get("decision") == "rejected":
        groups["editorial"].append("The reviewer rejected the video: " + str(overall.get("notes") or "no reason given")[:300])
    elif overall.get("decision") != "approved":
        groups["editorial"].append("The overall video decision is not 'approved'.")
    elif not _notes_ok(overall.get("notes")):
        groups["editorial"].append(f"The overall approval needs decision notes of at least {MIN_NOTE_CHARS} characters.")

    confirmations = decisions.get("confirmations") if isinstance(decisions.get("confirmations"), dict) else {}
    target = {"editorial": "editorial", "factual": "research", "visual": "visual", "audio": "qa",
              "rights": "rights", "disclosure": "editorial"}
    for name in CONFIRMATIONS:
        value = confirmations.get(name)
        if value != "approved":
            groups[target[name]].append(
                f"Human {name} confirmation is {value!r}; it must be explicitly 'approved'."
                if value is not None else f"Human {name} confirmation is missing.")

    scenes = [scene for scene in (manifest.get("scenes") or []) if isinstance(scene, dict)]
    scene_reviews, duplicates = _index(decisions.get("scene_reviews"), "scene_id")
    for identifier in duplicates:
        groups["visual"].append(f"Scene {identifier} has more than one decision.")
    known_scenes = {str(scene.get("scene_id")) for scene in scenes}
    for identifier in sorted(set(scene_reviews) - known_scenes):
        groups["identity"].append(f"The decisions mention scene {identifier!r}, which is not in this package.")
    for scene in scenes:
        scene_id = str(scene.get("scene_id"))
        review = scene_reviews.get(scene_id)
        if review is None:
            groups["visual"].append(f"{scene_id}: no shot decision was recorded.")
        elif review.get("decision") == "rejected":
            groups["visual"].append(f"{scene_id}: the shot was rejected: " + str(review.get("notes") or "no reason given")[:200])
        elif review.get("decision") != "approved":
            groups["visual"].append(f"{scene_id}: the shot decision is {review.get('decision')!r}, not 'approved'.")
        elif not _notes_ok(review.get("notes")):
            groups["visual"].append(f"{scene_id}: approval needs inspection notes of at least {MIN_NOTE_CHARS} characters.")

    asset_reviews, duplicates = _index(decisions.get("asset_reviews"), "asset_id")
    for identifier in duplicates:
        groups["rights"].append(f"Asset {identifier} has more than one decision.")
    assets = [asset for scene in scenes for asset in (scene.get("assets") or []) if isinstance(asset, dict)]
    known_assets = {str(asset.get("asset_id")) for asset in assets}
    for identifier in sorted(set(asset_reviews) - known_assets):
        groups["identity"].append(f"The decisions mention asset {identifier!r}, which is not in this package.")
    for asset in assets:
        asset_id = str(asset.get("asset_id"))
        review = asset_reviews.get(asset_id)
        provenance = asset.get("provenance") if isinstance(asset.get("provenance"), dict) else {}
        rights = asset.get("rights") if isinstance(asset.get("rights"), dict) else {}
        if review is None:
            groups["source"].append(f"{asset_id}: no source decision was recorded.")
            groups["rights"].append(f"{asset_id}: no rights decision was recorded.")
            continue
        if review.get("source_verified") is not True:
            groups["source"].append(f"{asset_id}: the reviewer did not confirm the original source "
                                    "(source_verified must be true).")
        decision = review.get("rights_decision")
        if decision == "rejected":
            groups["rights"].append(f"{asset_id}: rights were rejected: " + str(review.get("notes") or "no reason given")[:200])
        elif decision != "approved":
            groups["rights"].append(f"{asset_id}: the rights decision is {decision!r}, not 'approved'.")
        if not _notes_ok(review.get("notes")):
            groups["rights"].append(f"{asset_id}: the source and rights review needs notes of at least "
                                    f"{MIN_NOTE_CHARS} characters.")
        stated = str(review.get("license") or "").strip()
        recorded = str(rights.get("license") or rights.get("licence") or "").strip()
        if stated and recorded and stated.lower() != recorded.lower():
            groups["rights"].append(f"{asset_id}: the reviewer's licence {stated!r} differs from the recorded "
                                    f"licence {recorded!r}. Correct the build; a review cannot change rights evidence.")
        if provenance.get("origin") == "source":
            licence_url = str(rights.get("license_url") or rights.get("licence_url")
                              or review.get("license_url") or "").strip()
            if not is_http_url(licence_url) or looks_like_search_url(licence_url):
                groups["attribution"].append(f"{asset_id}: no licence or terms URL is recorded or supplied.")
            attribution = str(rights.get("attribution") or review.get("attribution") or "").strip()
            if len(attribution) < 3:
                groups["attribution"].append(f"{asset_id}: attribution text is missing.")

    research = manifest.get("research") if isinstance(manifest.get("research"), dict) else {}
    for kind, key, wanted, label in (("claims", "claim_id", "verified", "claim"),
                                     ("sources", "source_id", "reviewed", "research source")):
        reviews, _ = _index(decisions.get(f"{kind[:-1]}_reviews"), key)
        for item in research.get(kind) or []:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get(key))
            review = reviews.get(identifier)
            if review is None:
                groups["research"].append(f"{label} {identifier}: no decision was recorded.")
            elif review.get("decision") != wanted:
                groups["research"].append(f"{label} {identifier}: decision is {review.get('decision')!r}, not {wanted!r}.")
            elif len(str(review.get("notes") or "").strip()) < 5:
                groups["research"].append(f"{label} {identifier}: reviewer notes are required.")

    qa_reviews = decisions.get("qa_reviews") if isinstance(decisions.get("qa_reviews"), dict) else {}
    for name in HUMAN_RESOLVABLE_QA:
        status = statuses.get(name, "NOT_RUN")
        if status == "PASS":
            continue
        if status != "REVIEW":
            continue  # FAIL / NOT_RUN / INVALID are reported by the technical evaluation
        review = qa_reviews.get(name) if isinstance(qa_reviews.get(name), dict) else {}
        if review.get("decision") == "rejected":
            groups["qa"].append(f"qa/{name}.json: the reviewer rejected this check: " + str(review.get("notes") or "")[:200])
        elif review.get("decision") != "accepted" or not _notes_ok(review.get("notes")):
            groups["qa"].append(f"qa/{name}.json is REVIEW: a human must accept it with notes of at least "
                                f"{MIN_NOTE_CHARS} characters.")
    return groups


# ---------------------------------------------------------------------------
# Release evaluation
# ---------------------------------------------------------------------------
def _check(name: str, problems: list[str], passed_detail: str) -> dict:
    return {"name": name, "status": "BLOCKED" if problems else "PASS",
            "detail": "; ".join(problems) if problems else passed_detail, "problems": list(problems)}


def evaluate_release(package_dir, decisions: Any = None, *, expected_sha256: str | None = None,
                     expected_mode: str | None = None, expected_job_id: str | None = None,
                     expected_run_id: str | None = None, decode: bool = True,
                     now: datetime | None = None) -> dict:
    """Decide whether this exact package may be uploaded privately.

    Pure evaluation: reads the package, changes nothing, uploads nothing.  The
    result lists every blocking reason, grouped by independent check, so the
    reviewer sees all of them at once.
    """
    root = Path(package_dir).resolve()
    report = {"schema_version": RELEASE_REPORT_SCHEMA_VERSION, "evaluated_at": now_iso(),
              "allowed": False, "checks": [], "blocking": [],
              "job_id": None, "mode": None, "topic": None, "video_sha256": None,
              "manifest_fingerprint": None, "reviewer": None}

    def finish():
        report["blocking"] = [f"{check['name']}: {problem}" for check in report["checks"]
                              for problem in check["problems"]]
        report["allowed"] = bool(report["checks"]) and not report["blocking"]
        return report

    try:
        manifest = read_manifest(root)
    except ContractError as exc:
        report["checks"].append(_check("package", [str(exc)], ""))
        return finish()
    video_meta = manifest.get("video") if isinstance(manifest.get("video"), dict) else {}
    report.update({"job_id": manifest.get("job_id"), "mode": manifest.get("mode"),
                   "topic": manifest.get("topic"), "manifest_fingerprint": manifest.get("fingerprint"),
                   "source": manifest.get("source")})
    structure = manifest_structure_problems(manifest)
    report["checks"].append(_check("package", structure, "Manifest schema v2 with job id, video hash and fingerprint."))
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return finish()

    # --- identity of the artifact --------------------------------------------------
    identity = []
    actual_sha = None
    probe = None
    try:
        video_path = inside(root, video_meta.get("file") or manifest.get("video_file") or "video.mp4")
        probe = probe_video(video_path)
        actual_sha = sha256_file(video_path)
    except ContractError as exc:
        identity.append(str(exc))
    report["video_sha256"] = actual_sha
    if actual_sha:
        if actual_sha != str(video_meta.get("sha256") or "").lower():
            identity.append("Changed video: the MP4 in the package does not match the hash recorded when it was built.")
        if expected_sha256 is not None and actual_sha != str(expected_sha256).strip().lower():
            identity.append("The MP4 SHA-256 does not match the hash given for this release.")
    if expected_mode is not None and manifest.get("mode") != expected_mode:
        identity.append(f"Wrong production mode: the package is {manifest.get('mode')!r}, the release was "
                        f"requested for {expected_mode!r}.")
    if expected_job_id is not None and str(manifest.get("job_id")) != str(expected_job_id):
        identity.append(f"Wrong job: the package is job {manifest.get('job_id')!r}, not {expected_job_id!r}.")
    if expected_run_id is not None:
        built_by = str((manifest.get("source") or {}).get("workflow_run_id") or "")
        if built_by != str(expected_run_id):
            identity.append(f"Wrong artifact: the package was built by workflow run {built_by or 'unknown'}, "
                            f"not the selected run {expected_run_id}.")
    report["checks"].append(_check("artifact identity", identity,
                                   "Job, mode, source run and MP4 SHA-256 match the request."))

    # --- technical QA (never overridable) ------------------------------------------
    technical = []
    if probe:
        technical.extend(profile_problems(str(manifest.get("mode")), probe))
        if decode:
            decoded = decode_check(video_path, probe.get("duration_seconds"))
            if not decoded["decoded"]:
                technical.append(decoded["detail"])
    statuses = qa_statuses(root)
    report["qa_statuses"] = statuses
    for name in TECHNICAL_QA:
        if statuses.get(name) != "PASS":
            technical.append(f"qa/{name}.json is {statuses.get(name)}; technical QA must be PASS and "
                             "cannot be overridden by a reviewer.")
    for name in HUMAN_RESOLVABLE_QA:
        if statuses.get(name) not in {"PASS", "REVIEW"}:
            technical.append(f"qa/{name}.json is {statuses.get(name)}; a FAIL or missing report cannot be "
                             "resolved by a reviewer.")
    try:
        captions = inside(root, (manifest.get("captions") or {}).get("file") or manifest.get("captions_file"))
        if not captions.is_file() or captions.stat().st_size == 0:
            technical.append("The captions file is missing or empty.")
    except ContractError as exc:
        technical.append(str(exc))
    for scene in manifest.get("scenes") or []:
        for asset in (scene.get("assets") or []) if isinstance(scene, dict) else []:
            try:
                path = inside(root, asset.get("path"))
            except ContractError as exc:
                technical.append(str(exc))
                continue
            if not path.is_file():
                technical.append(f"{asset.get('asset_id')}: the asset file is missing from the package.")
            elif asset.get("sha256") and sha256_file(path) != asset.get("sha256"):
                technical.append(f"{asset.get('asset_id')}: the asset file changed after the package was built.")
    report["checks"].append(_check("automated QA", technical,
                                   "The MP4 decodes, meets the delivery profile, and every QA report is PASS or REVIEW."))

    # --- rights evidence recorded by the build ------------------------------------
    evidence = []
    for scene in manifest.get("scenes") or []:
        for asset in (scene.get("assets") or []) if isinstance(scene, dict) else []:
            evidence.extend(rights_evidence_problems(asset))
    report["checks"].append(_check("rights evidence", evidence,
                                   "Every asset has a recorded origin and, for source assets, an accepted licence."))

    # --- human decisions ----------------------------------------------------------
    if decisions is None:
        stored = root / DECISIONS_NAME
        decisions = read_json(stored, what="review decisions") if stored.is_file() else None
    if decisions is None:
        missing = ["No human review decisions were supplied; nothing is approved."]
        for name in ("review binding", "editorial review", "visual review", "source review",
                     "rights review", "attribution", "research review", "QA review"):
            report["checks"].append(_check(name, missing if name == "review binding" else
                                           ["Not reviewed."], ""))
        return finish()
    groups = decision_problems(manifest, decisions, statuses, now=now)
    if isinstance(decisions, dict):
        report["reviewer"] = str(decisions.get("reviewer") or "").strip() or None
        report["reviewed_at"] = decisions.get("reviewed_at")
    for name, key, detail in (
            ("review binding", "identity", "Decisions are bound to this job, manifest and MP4 hash."),
            ("editorial review", "editorial", "Named reviewer approved the video with notes."),
            ("visual review", "visual", "Every shot was approved with inspection notes."),
            ("source review", "source", "Every asset's original source was confirmed."),
            ("rights review", "rights", "Every asset's rights were approved with notes."),
            ("attribution", "attribution", "Licence URL and attribution are complete."),
            ("research review", "research", "Every claim and research source was reviewed."),
            ("QA review", "qa", "Every automated REVIEW was accepted with notes.")):
        report["checks"].append(_check(name, groups[key], detail))
    return finish()


def summarize(report: dict) -> str:
    """Markdown summary of a release evaluation for a job summary."""
    lines = [f"## Release evaluation: {'ALLOWED' if report.get('allowed') else 'BLOCKED'}", "",
             f"- Job: `{report.get('job_id')}` ({report.get('mode')})",
             f"- Topic: {report.get('topic')}",
             f"- MP4 SHA-256: `{report.get('video_sha256')}`",
             f"- Reviewer: {report.get('reviewer') or 'none'}", "",
             "| Check | Result | Detail |", "|---|---|---|"]
    for check in report.get("checks") or []:
        detail = str(check.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check['name']} | {check['status']} | {detail[:600]} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Package status and assembly
# ---------------------------------------------------------------------------
_STRUCTURAL_CHECKS = ("package", "artifact identity", "automated QA")


def package_status(package_dir, *, decode: bool = True) -> dict:
    """Summary of a v2 package for ``package_summary.json``.

    ``FAIL``              the package itself is broken (bad MP4, missing file,
                          automated QA failed); a build must not report success.
    ``REVIEW_REQUIRED``   a valid review package that is not (yet) releasable.
    ``APPROVED_FOR_UPLOAD`` stored decisions authorise this exact video.
    """
    root = Path(package_dir).resolve()
    report = evaluate_release(root, decode=decode)
    errors, reviews = [], []
    for check in report["checks"]:
        target = errors if check["name"] in _STRUCTURAL_CHECKS else reviews
        target.extend(f"{check['name']}: {problem}" for problem in check["problems"])
    preview = root / "preview" / "frames"
    if not preview.is_dir() or not any(preview.iterdir()):
        errors.append("package: preview/frames is missing; there is no visual inspection evidence.")
    status = "FAIL" if errors else ("REVIEW_REQUIRED" if reviews else "APPROVED_FOR_UPLOAD")
    manifest = {}
    try:
        manifest = read_manifest(root)
    except ContractError:
        pass
    return {
        "schema_version": 2, "status": status, "mode": report.get("mode"), "topic": report.get("topic"),
        "job_id": report.get("job_id"), "video_sha256": report.get("video_sha256"),
        "manifest_fingerprint": report.get("manifest_fingerprint"),
        "errors": errors, "warnings": [], "required_human_reviews": reviews,
        "technical_probe": {key: (manifest.get("video") or {}).get(key) for key in
                            ("width", "height", "duration_seconds", "fps", "video_codec", "audio_codec")},
        "qa_statuses": report.get("qa_statuses") or {},
        "checks": [{"name": check["name"], "status": check["status"]} for check in report["checks"]],
        "publication_eligible": status == "APPROVED_FOR_UPLOAD",
        "notice": ("Technical validation is not editorial approval. APPROVED_FOR_UPLOAD requires human "
                   "decisions bound to this job and to the SHA-256 of this exact MP4."),
    }


def write_package(package_dir, manifest: dict, *, checklist_title: str = "Human review required",
                  checklist_items: tuple[str, ...] = ()) -> dict:
    """Write the manifest, the pending checklist, the summary and the dashboard.

    A freshly built package can only ever be ``REVIEW_REQUIRED`` (or ``FAIL``):
    this function removes any decision file left in the directory and raises if
    the result claims approval.
    """
    root = Path(package_dir).resolve()
    for stale in (DECISIONS_NAME, RELEASE_REPORT_NAME, "youtube-upload-result.json"):
        try:
            (root / stale).unlink()
        except OSError:
            pass
    write_json(root / MANIFEST_NAME, manifest)
    checklist = {name: "pending" for name in CONFIRMATIONS}
    checklist.update({"human_review_status": "pending", "job_id": manifest.get("job_id"),
                      "video_sha256": (manifest.get("video") or {}).get("sha256")})
    write_json(root / "review_checklist.json", checklist)
    items = checklist_items or (
        "Watch the complete video from beginning to end",
        "Check every shot against its narration",
        "Open each original source page and confirm the licence and attribution",
        "Listen to the narration and the mix; read the captions against the audio",
        "Confirm the facts and the AI-content disclosure",
    )
    (root / "review_checklist.md").write_text(
        f"# {checklist_title}\n\n" + "".join(f"- [ ] {item}\n" for item in items)
        + "\nOpen `review_dashboard.html` to record and export your decisions.\n"
          "**This package is review-only. Nothing was uploaded.**\n", encoding="utf-8")
    from core.review_dashboard import build_dashboard
    build_dashboard(root)
    summary = package_status(root)
    write_json(root / "package_summary.json", summary)
    if summary["status"] == "APPROVED_FOR_UPLOAD":
        raise ContractError("A newly built package must not be approved; refusing to write it.")
    return summary


def extract_preview_frames(video, frames_dir, count: int = 6, duration_seconds: float | None = None) -> int:
    """Evenly spaced stills from the rendered video for the package preview."""
    ffmpeg, _ = _tools()
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = duration_seconds or probe_video(video)["duration_seconds"]
    written = 0
    for index in range(count):
        moment = duration * (index + 0.5) / count
        target = frames_dir / f"frame_{index + 1:03d}.jpg"
        result = subprocess.run(
            [ffmpeg, "-y", "-nostdin", "-loglevel", "error", "-ss", f"{moment:.3f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "4", str(target)],
            capture_output=True, text=True, check=False, timeout=120)
        if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            written += 1
    if not written:
        raise ContractError("Could not extract any preview frame from the rendered video.")
    return written


def load_decisions_text(text: str) -> dict:
    """Parse decisions pasted into a workflow input: JSON, or ``gz64:<base64 gzip>``."""
    import base64
    import gzip
    raw = str(text or "").strip()
    if not raw:
        raise ContractError("No review decisions were supplied.")
    if raw.startswith("gz64:"):
        try:
            packed = base64.b64decode(raw[5:], validate=True)
            # Bounded decompression: a decision file is small; refuse a decompression bomb.
            with gzip.GzipFile(fileobj=__import__("io").BytesIO(packed)) as handle:
                data = handle.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ContractError("The review decisions are larger than 8 MB after decompression.")
            raw = data.decode("utf-8")
        except ContractError:
            raise
        except Exception as exc:
            raise ContractError(f"The compressed review decisions could not be decoded: {exc}") from exc
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ContractError(f"The review decisions are not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("The review decisions must be a JSON object.")
    return value


def find_packages(root) -> list[Path]:
    """Every schema-v2 review package under ``root`` (a downloaded run artifact).

    A package is recognised by its contents, not by its folder name: a
    ``production_manifest.json`` with ``schema_version`` 2 and a known mode,
    next to a ``video.mp4``.  Render-time manifests of other shapes are ignored.
    """
    root = Path(root)
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.rglob(MANIFEST_NAME)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema_version") == MANIFEST_SCHEMA_VERSION and data.get("mode") in MODES \
                and (path.parent / "video.mp4").is_file():
            found.append(path.parent)
    return found
