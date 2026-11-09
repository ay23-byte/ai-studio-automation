"""End-to-end archival-video retrieval audit for one real documentary scene.

This command downloads at most one rights-filtered archive candidate, probes the
actual media file, checks its item-level provenance sidecar, and builds a visual
review manifest. It never uses stock footage or generated video as a fallback.
A successful technical audit still requires human authenticity and rights review.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ARCHIVAL_PROVIDERS = {
    "NASA", "National Archives (NARA)", "DVIDS", "Library of Congress",
    "Wikimedia Commons", "Internet Archive", "Europeana",
    "Europeana CC",
}


def _probe_video(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size < 50_000:
        raise ValueError("Downloaded archive candidate is missing or smaller than 50 KB.")
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of", "json", str(path),
    ], text=True)
    data = json.loads(raw)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not video or duration < 2:
        raise ValueError("Downloaded archive candidate is not a valid video of at least two seconds.")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width < 256 or height < 256:
        raise ValueError(f"Archive video resolution is too small: {width}x{height}.")
    return {"duration_seconds": round(duration, 3), "width": width, "height": height}


def audit_archival_asset(video_path: str | Path, query: str, period: str,
                         output_dir: str | Path) -> dict:
    from core.visual_production import build_visual_review

    video = Path(video_path)
    sidecar = video.with_suffix(video.suffix + ".source.json")
    if not sidecar.is_file():
        raise ValueError("Archive video has no .source.json provenance sidecar.")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if metadata.get("provider") not in ARCHIVAL_PROVIDERS:
        raise ValueError(f"Unexpected source provider for archival footage: {metadata.get('provider')!r}.")
    if metadata.get("asset_type") != "archival_video_candidate" or not metadata.get("archival_material"):
        raise ValueError("Source sidecar does not identify this as an archival-video candidate.")
    if metadata.get("archival_authenticity_status") != "pending_human_verification":
        raise ValueError("Authenticity must remain explicitly pending human verification.")
    if not metadata.get("page_url") or not metadata.get("licence") or not metadata.get("licence_url"):
        raise ValueError("Item page, licence and licence URL are mandatory provenance fields.")
    if not metadata.get("relevance_score") or float(metadata["relevance_score"]) < 0.25:
        raise ValueError("Archive candidate did not meet the scene relevance threshold.")

    probe = _probe_video(video)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    timeline = [{
        "visual_type": "archival",
        "archival_required": True,
        "time_period": period,
        "subject": query,
        "visual_prompt": f"Review the actual archive clip for: {query}; requested period: {period}",
        "_start": 0.0,
        "_dur": probe["duration_seconds"],
        "_media": {"type": "video", "path": str(video.resolve())},
    }]
    visual = build_visual_review(timeline, root, sample_video_frames=True)
    shot = visual["shots"][0]
    provenance_complete = bool(shot.get("archival_provenance_complete"))
    result = {
        "status": "passed" if provenance_complete else "failed",
        "query": query,
        "requested_period_for_human_comparison": period,
        "asset": str(video.resolve()),
        "source_sidecar": str(sidecar.resolve()),
        "provider": metadata.get("provider"),
        "title": metadata.get("title"),
        "item_page": metadata.get("page_url"),
        "licence": metadata.get("licence"),
        "licence_url": metadata.get("licence_url"),
        "relevance_score": metadata.get("relevance_score"),
        "probe": probe,
        "visual_review_report": str(root / "visual_review_report.json"),
        "sample_frames": shot.get("sample_frames", []),
        "archival_provenance_fields": shot.get("archival_provenance_fields"),
        "archival_provenance_complete": provenance_complete,
        "archival_authenticity_status": "pending_human_verification",
        "human_review_required": True,
        "publication_ready": False,
        "stock_or_ai_substitution_used": False,
        "youtube_upload_performed": False,
        "notice": "Technical validation does not prove that the footage depicts the claimed event or period. A human must inspect the full clip, archive record, third-party content and exact reuse rights before publication.",
    }
    (root / "archival-video-e2e-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Specific historical subject/event for the archive search.")
    parser.add_argument("--period", required=True, help="Requested period to compare with the archive record, e.g. 1986.")
    parser.add_argument("--output-dir", default="build/archival_video_e2e")
    parser.add_argument(
        "--reuse-existing", action="store_true",
        help="Audit an existing archival-candidate.mp4 and sidecar instead of making a network request.",
    )
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    video = output / "archival-candidate.mp4"
    sidecar = video.with_suffix(video.suffix + ".source.json")

    if args.reuse_existing:
        if not video.is_file() or not sidecar.is_file():
            raise SystemExit("--reuse-existing requires both the candidate MP4 and its provenance sidecar.")
    else:
        video.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        from core.footage_sources import fetch_documentary_clip
        found = fetch_documentary_clip(
            [args.query], video, set(),
            scene_prompt=f"genuine archival footage newsreel historical film {args.query}",
        )
        if not found:
            report = {
                "status": "failed",
                "query": args.query,
                "requested_period_for_human_comparison": args.period,
                "reason": "No qualifying archival video was retrieved. Generic stock and AI video fallback are deliberately disabled.",
                "stock_or_ai_substitution_used": False,
                "human_review_required": True,
                "youtube_upload_performed": False,
            }
            (output / "archival-video-e2e-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit(1)

    result = audit_archival_asset(video, args.query, args.period, output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit("Archive asset was retrieved, but provenance is incomplete. Human review is still required.")


if __name__ == "__main__":
    main()
