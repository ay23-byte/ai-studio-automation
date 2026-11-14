"""Live verification of every footage provider: search, download, ffprobe.

A provider is reported LIVE-VERIFIED only when all of this happened in this run:

1. its API answered a real search,
2. a file was downloaded from a result whose licence the policy accepts,
3. ffprobe read the file and its frames decoded.

Anything less gets the status that describes where it stopped. Nothing is
uploaded or published, and each provider is asked for one small clip.

    python -m scripts.verify_footage_providers --output-dir build/footage_live_verification
    python -m scripts.verify_footage_providers --providers "NASA,Wikimedia Commons" --strict

The command needs network access to the providers. Keyed providers are read from
DVIDS_API_KEY, EUROPEANA_API_KEY, NARA_API_KEY, COVERR_API_KEY and PIXABAY_API_KEY.
"""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

LIVE_VERIFIED = "LIVE-VERIFIED"
NOT_CONFIGURED = "NOT-CONFIGURED"
AUTH_FAILED = "AUTH-FAILED"
RATE_LIMITED = "RATE-LIMITED"
UNREACHABLE = "UNREACHABLE"
NO_ELIGIBLE_RESULTS = "NO-ELIGIBLE-RESULTS"
DOWNLOAD_FAILED = "DOWNLOAD-FAILED"
INVALID_MEDIA = "INVALID-MEDIA"
MANUAL_ONLY = "MANUAL-IMPORT-ONLY"

# Short, concrete searches each catalogue is known to hold film for. They test
# the adapter, not a documentary topic; --topic adds the caller's own subject.
PROVIDER_QUERIES = {
    "NASA": ["Apollo 11 launch", "space shuttle launch", "International Space Station"],
    "Wikimedia Commons": ["steam locomotive", "city street traffic", "river timelapse"],
    "Internet Archive": ["city traffic", "steam train", "ocean waves"],
    "Library of Congress": ["locomotive", "New York street", "parade"],
    "DVIDS": ["helicopter flight", "training exercise", "aircraft carrier"],
    "Europeana": ["steam locomotive", "harbour ships", "city tram"],
    "National Archives (NARA)": ["space shuttle", "army training film", "navy ships"],
    "Coverr": ["mountain river", "city traffic", "ocean waves"],
    "Pixabay": ["mountain river", "city traffic", "ocean waves"],
}
SCENES = [
    ("rocket-launch", ["rocket launch", "space shuttle launch"], "A rocket lifts off from the launch pad"),
    ("steam-train", ["steam locomotive", "steam train"], "A steam locomotive pulls a train through the countryside"),
    ("city-traffic", ["city traffic", "city street"], "Traffic moves through a busy city street"),
    ("ocean", ["ocean waves", "sea coast"], "Waves break on a rocky coast"),
    ("helicopter", ["helicopter flight", "military helicopter"], "A helicopter flies low over the land"),
    ("harbour", ["harbour ships", "cargo ship port"], "Ships move through a working harbour"),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def all_providers(scene_prompt: str = ""):
    """Every provider behind the common interface, NASA included regardless of topic."""
    from core import footage_sources as fs
    from core import images
    providers = fs.archive_providers("space nasa")
    return providers + images.stock_providers(scene_prompt)


def classify(entry: dict) -> tuple[str, str]:
    """Turn one provider's counters into a status and a one-line explanation."""
    reason = str(entry.get("failure_reason") or entry.get("disabled_reason") or "")
    if entry.get("download_successes"):
        return LIVE_VERIFIED, "a real search, a download and ffprobe validation all succeeded in this run"
    if entry.get("auth_status") == "missing_key":
        return NOT_CONFIGURED, entry.get("disabled_reason") or "the API key is not set"
    counts = entry.get("http_status_counts") or {}
    if entry.get("auth_status") == "rejected" or any(code in counts for code in ("401", "403")):
        return AUTH_FAILED, reason or "the provider refused the credentials"
    search_status = str(entry.get("search_status") or "")
    if search_status == "rate_limited" or "429" in counts:
        return RATE_LIMITED, reason or "the provider answered HTTP 429"
    if search_status in ("network_error", "timeout", "blocked", "server_error", "error", "invalid_response"):
        return UNREACHABLE, reason or f"search ended with {search_status}"
    if entry.get("invalid_media"):
        return INVALID_MEDIA, reason or "downloaded files failed ffprobe validation"
    if entry.get("download_attempts"):
        return DOWNLOAD_FAILED, reason or "no download completed"
    if entry.get("results_found") or entry.get("downloadable_candidates"):
        return NO_ELIGIBLE_RESULTS, (
            f"{entry.get('results_found', 0)} result(s), {entry.get('licence_rejections', 0)} refused by the licence "
            f"policy, {entry.get('downloadable_candidates', 0)} with a usable file; none relevant enough to download")
    return NO_ELIGIBLE_RESULTS, reason or "the search returned nothing for the test queries"


def _sample_frame(clip: Path, target: Path) -> str:
    """One still from the middle of the clip so a person can see what was fetched."""
    try:
        from core.ffmpeg_utils import ffmpeg_bin
        from core.footage_validation import inspect_media
        middle = max(0.0, float(inspect_media(clip).get("duration_seconds") or 0) / 2)
        subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{middle:.2f}", "-i", str(clip),
                        "-frames:v", "1", "-vf", "scale=640:-2", str(target)],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return str(target) if target.is_file() else ""
    except Exception:
        return ""


def verify_providers(output_dir, *, topic: str = "", only=None, providers=None, keep_clips: bool = False,
                     download=None) -> dict:
    """Run search, download and validation once per provider. Returns the verification report."""
    from core import footage_report, footage_sources as fs
    from core.asset_licensing import build_licence_register
    from core.footage_engine import acquire

    out = Path(output_dir)
    clips = out / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    fs.reset_run_state()
    report = footage_report.begin_run("live provider verification")
    wanted = {name.strip().lower() for name in (only or []) if name.strip()}
    results, shots = [], []
    for provider in providers if providers is not None else all_providers():
        if wanted and provider.name.lower() not in wanted:
            continue
        queries = ([topic] if topic else []) + PROVIDER_QUERIES.get(provider.name, ["city street", "river"])
        clip = clips / f"{_slug(provider.name)}.mp4"
        clip.unlink(missing_ok=True)
        selected = acquire(
            queries, clip, set(), scene_prompt=queries[0], providers=[provider],
            download=download or fs._download, relevance=fs._subject_relevance, min_relevance=fs.minimum_relevance(),
            report=report, scene_label=provider.name)
        entry = report.provider(provider.name)
        status, explanation = classify(entry)
        record = {"provider": provider.name, "status": status, "explanation": footage_report.redact(explanation),
                  "queries": queries, "counters": dict(entry)}
        scene = next((item for item in reversed(report.scenes) if item.get("scene") == provider.name), None)
        if scene and scene.get("reason"):
            record["scene_reason"] = footage_report.redact(str(scene["reason"]))[:400]
        sidecar = clip.with_suffix(clip.suffix + ".source.json")
        if selected and clip.is_file() and sidecar.is_file():
            metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            record["asset"] = {key: metadata.get(key) for key in (
                "asset_id", "title", "creator", "page_url", "licence", "licence_url", "licence_state",
                "attribution_text", "download_url", "width", "height", "duration_seconds", "file_size_bytes",
                "container", "video_codec", "media_sha256", "relevance_score", "matched_query")}
            (out / "frames").mkdir(parents=True, exist_ok=True)
            record["sample_frame"] = _sample_frame(clip, out / "frames" / f"{_slug(provider.name)}.jpg")
            shots.append({"_start": 120.0 * len(shots), "_media": {"type": "video", "path": str(clip)}})
        results.append(record)

    results.append({
        "provider": "Mixkit", "status": MANUAL_ONLY,
        "explanation": ("Mixkit has no public API and its terms forbid automated downloading. Clips are downloaded "
                        "by a person and imported with scripts/register_mixkit_asset.py, which validates the file."),
    })
    register = build_licence_register(shots, out) if shots else None
    written = report.write(out)
    if not keep_clips:
        # Reports, sidecars and stills are the evidence; the media itself is not redistributed.
        for clip in clips.glob("*.mp4"):
            clip.unlink(missing_ok=True)
    verified = [item["provider"] for item in results if item["status"] == LIVE_VERIFIED]
    verification = {
        "schema_version": 1,
        "what_live_verified_means": "a real search, a real download and ffprobe validation succeeded in this run",
        "live_verified_providers": verified,
        "live_verified_count": len(verified),
        "providers": results,
        "provider_report": written["json_path"],
        "licence_register": (register or {}).get("csv_path"),
        "credits": (register or {}).get("credits_path"),
        "clips_kept": bool(keep_clips),
        "uploaded_or_published": False,
    }
    (out / "live_verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    (out / "live_verification.md").write_text(render_markdown(verification), encoding="utf-8")
    return verification


def verify_selection(output_dir, *, scenes=None) -> dict:
    """Run a small multi-scene selection through the production entry point.

    Shows whether a documentary draws on several providers or collapses onto one.
    """
    from core import footage_report, footage_sources as fs
    from core import images
    out = Path(output_dir) / "selection"
    out.mkdir(parents=True, exist_ok=True)
    fs.reset_run_state()
    report = footage_report.begin_run("live multi-scene selection")
    used: set = set()
    for label, keywords, prompt in scenes or SCENES:
        images.fetch_clip(keywords, out / f"{label}.mp4", used, scene_prompt=prompt)
    written = report.write(out)
    for clip in out.glob("*.mp4"):
        clip.unlink(missing_ok=True)
    return written["summary"]


def render_markdown(verification: dict) -> str:
    lines = ["# Live footage provider verification", "",
             "LIVE-VERIFIED means a real search, a real download and ffprobe validation all succeeded in this run.",
             "", "| Provider | Status | Asset | Licence | Size | Explanation |", "|---|---|---|---|---|---|"]
    for item in verification.get("providers") or []:
        asset = item.get("asset") or {}
        size = f"{asset.get('width')}x{asset.get('height')}, {asset.get('duration_seconds')}s" if asset else "-"
        lines.append("| " + " | ".join(str(value or "-").replace("|", "/").replace("\n", " ") for value in (
            item.get("provider"), item.get("status"), str(asset.get("title") or "")[:50],
            asset.get("licence"), size, str(item.get("explanation") or "")[:220])) + " |")
    selection = verification.get("selection")
    if selection:
        lines += ["", "## Multi-scene selection", "",
                  f"- Scenes with validated footage: {selection.get('scenes_with_footage')} of "
                  f"{selection.get('scenes_searched')}",
                  f"- Selected by provider: {selection.get('selected_by_provider')}"]
    lines += ["", "Nothing was uploaded or published. Every asset still needs human rights and context review."]
    return "\n".join(lines) + "\n"


def _annotate(verification: dict):
    """One GitHub Actions notice per provider, so the result is visible on the run page."""
    if os.getenv("GITHUB_ACTIONS", "").lower() != "true":
        return

    def clean(text):
        return str(text).replace("%", "%25").replace("\r", " ").replace("\n", " ")

    for item in verification.get("providers") or []:
        asset = item.get("asset") or {}
        counters = item.get("counters") or {}
        if asset:
            detail = (f"\"{str(asset.get('title') or '')[:60]}\" {asset.get('width')}x{asset.get('height')} "
                      f"{asset.get('duration_seconds')}s {asset.get('video_codec')}, {asset.get('licence')} "
                      f"({asset.get('licence_state')}), relevance {asset.get('relevance_score')}")
        else:
            detail = str(item.get("explanation") or "")
        if counters:
            detail += (f" | results {counters.get('results_found', 0)}, usable {counters.get('downloadable_candidates', 0)}, "
                       f"licence-refused {counters.get('licence_rejections', 0)}, downloads "
                       f"{counters.get('download_successes', 0)}/{counters.get('download_attempts', 0)}, "
                       f"invalid {counters.get('invalid_media', 0)}, HTTP {counters.get('http_status_counts') or {}}")
            if item.get("scene_reason") and not asset:
                detail += f" | {item['scene_reason']}"
        level = "notice" if item.get("status") in (LIVE_VERIFIED, MANUAL_ONLY, NOT_CONFIGURED) else "warning"
        print(f"::{level} title=Footage provider {item.get('provider')}::{clean(item.get('status'))}: {clean(detail)[:900]}")
    selection = verification.get("selection")
    if selection:
        print("::notice title=Footage multi-scene selection::" + clean(
            f"{selection.get('scenes_with_footage')} of {selection.get('scenes_searched')} scenes received validated "
            f"footage; by provider: {selection.get('selected_by_provider')}; "
            f"manual review: {selection.get('scenes_needing_manual_review')}"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default="build/footage_live_verification")
    parser.add_argument("--topic", default="", help="Extra search phrase tried first for every provider.")
    parser.add_argument("--providers", default="", help="Comma-separated provider names; default is all.")
    parser.add_argument("--min-verified", type=int, default=1,
                        help="Fail unless at least this many providers are LIVE-VERIFIED (default 1).")
    parser.add_argument("--strict", action="store_true",
                        help="Fail when any provider with credentials configured is not LIVE-VERIFIED.")
    parser.add_argument("--selection", action="store_true",
                        help="Also run a six-scene selection to measure provider diversity.")
    parser.add_argument("--keep-clips", action="store_true", help="Keep the downloaded clips (default: delete).")
    args = parser.parse_args(argv)

    verification = verify_providers(
        args.output_dir, topic=args.topic.strip(),
        only=[name for name in args.providers.split(",") if name.strip()], keep_clips=args.keep_clips)
    if args.selection:
        verification["selection"] = verify_selection(args.output_dir)
        out = Path(args.output_dir)
        (out / "live_verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        (out / "live_verification.md").write_text(render_markdown(verification), encoding="utf-8")
    print(render_markdown(verification))
    _annotate(verification)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(render_markdown(verification))

    failures = []
    if verification["live_verified_count"] < args.min_verified:
        failures.append(f"{verification['live_verified_count']} provider(s) LIVE-VERIFIED; "
                        f"{args.min_verified} required")
    if args.strict:
        for item in verification["providers"]:
            if item["status"] not in (LIVE_VERIFIED, NOT_CONFIGURED, MANUAL_ONLY):
                failures.append(f"{item['provider']}: {item['status']}")
    if failures:
        print("Live verification did not pass: " + "; ".join(failures), file=_sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
