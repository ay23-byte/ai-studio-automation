"""Structural and timing QA for rendered ASS captions in news Shorts."""
from __future__ import annotations

import json
import re
from pathlib import Path


def _seconds(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})\\.(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Invalid ASS timestamp: {value!r}")
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100


def _normalise(value: str) -> str:
    value = re.sub(r"\{[^}]*\}", "", value)
    return " ".join(value.replace("\\N", " ").replace("\\n", " ").split())


def audit_captions(manifest_path: str | Path) -> dict:
    manifest_path = Path(manifest_path)
    errors, warnings, rows = [], [], []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        shots = manifest.get("shots")
        caption_path = Path(manifest.get("captions") or manifest_path.parent / "captions.ass")
        if not isinstance(shots, list) or not shots:
            raise ValueError("Production manifest has no shots.")
        if not caption_path.is_file():
            raise FileNotFoundError(f"Caption file is missing: {caption_path}")
        raw = caption_path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return {"status": "FAIL", "errors": [f"{type(exc).__name__}: {exc}"],
                "warnings": [], "caption_count": 0, "human_review_required": True}

    if not re.search(r"(?m)^PlayResX:\s*1080\s*$", raw):
        errors.append("ASS PlayResX must be 1080.")
    if not re.search(r"(?m)^PlayResY:\s*1920\s*$", raw):
        errors.append("ASS PlayResY must be 1920.")
    if not re.search(r"(?m)^Style: News,.*?,2,90,90,300,1\s*$", raw):
        warnings.append("Expected compact lower-safe-band News subtitle style was not found.")

    events = []
    pending = None
    for line in raw.splitlines():
        if line.startswith("Dialogue:"):
            if pending is not None:
                events.append(pending)
            fields = line.split(",", 9)
            if len(fields) != 10:
                errors.append("Malformed ASS Dialogue event.")
                pending = None
                continue
            try:
                start, end = _seconds(fields[1]), _seconds(fields[2])
            except ValueError as exc:
                errors.append(str(exc))
                pending = None
                continue
            pending = {"start": start, "end": end, "text": fields[9]}
        elif pending is not None and line and not line.startswith("["):
            pending["text"] += " " + line.strip()
    if pending is not None:
        events.append(pending)
    for event in events:
        event["text"] = _normalise(event["text"])

    if len(events) != len(shots):
        errors.append(f"Expected one caption event per scene ({len(shots)}); found {len(events)}.")

    previous_end = 0.0
    for index, (shot, event) in enumerate(zip(shots, events), 1):
        try:
            expected_start = float(shot["start_seconds"])
            expected_end = expected_start + float(shot["duration_seconds"])
            if event["end"] <= event["start"]:
                errors.append(f"Scene {index} caption has non-positive duration.")
            if abs(event["start"] - expected_start) > 0.06:
                errors.append(f"Scene {index} caption starts {event['start']:.2f}s; expected {expected_start:.2f}s.")
            if abs(event["end"] - expected_end) > 0.06:
                errors.append(f"Scene {index} caption ends {event['end']:.2f}s; expected {expected_end:.2f}s.")
            if index > 1 and event["start"] < previous_end - 0.02:
                errors.append(f"Scene {index} caption overlaps the previous scene.")
            if index > 1 and event["start"] - previous_end > 0.55:
                warnings.append(f"Scene {index} has a caption gap of {event['start'] - previous_end:.2f}s.")
            expected_text = _normalise(str(shot.get("narration") or ""))
            if event["text"] != expected_text:
                errors.append(f"Scene {index} caption text does not match its narration.")
            rows.append({"scene": index, "start_seconds": round(event["start"], 2),
                         "end_seconds": round(event["end"], 2),
                         "text_matches_narration": event["text"] == expected_text})
            previous_end = event["end"]
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"Scene {index} manifest timing is invalid: {exc}")

    status = "FAIL" if errors else ("REVIEW" if warnings else "PASS")
    return {"status": status, "caption_count": len(events), "scene_count": len(shots),
            "captions": rows, "errors": errors, "warnings": warnings,
            "caption_text_and_timing_verified": status == "PASS",
            "human_review_required": True,
            "notice": "Structural checks cannot judge readability, visual obstruction, line breaks, language quality or perceived synchronization. Watch the complete video."}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    result = audit_captions(args.manifest)
    report = Path(args.report) if args.report else Path(args.manifest).parent / "advanced_qa" / "caption_qa_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result["status"] == "FAIL")
