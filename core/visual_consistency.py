"""Shot-level visual consistency and image-quality diagnostics.

Metrics are conservative editorial signals. They do not judge semantic relevance,
historical truth, authenticity, or legal rights.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _frame_metrics(path: str | Path) -> dict[str, Any]:
    from PIL import Image, ImageChops, ImageFilter, ImageStat

    frame_path = Path(path)
    with Image.open(frame_path) as source:
        width, height = source.size
        image = source.convert("RGB")
        image.thumbnail((160, 90))
        raw = image.tobytes()  # RGB triples (Image.getdata is removed in Pillow 14)
        pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
        count = max(len(pixels), 1)
        luminance = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels]
        brightness = sum(luminance) / count
        contrast = statistics.pstdev(luminance) if count > 1 else 0.0
        saturation = sum(max(rgb) - min(rgb) for rgb in pixels) / count
        clipped = sum(1 for value in luminance if value <= 5 or value >= 250) / count
        gray = image.convert("L")
        edges = ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(radius=1.0)))
        sharpness = ImageStat.Stat(edges).mean[0]
    return {
        "width": width, "height": height,
        "brightness": round(brightness, 2),
        "contrast": round(contrast, 2),
        "saturation": round(saturation, 2),
        "clipped_pixel_ratio": round(clipped, 4),
        "sharpness_proxy": round(sharpness, 2),
    }


def audit_visual_consistency(visual_report: dict[str, Any],
                             output_dir: str | Path) -> dict[str, Any]:
    """Measure sampled frames and flag severe quality problems and shot outliers."""
    rows, issues = [], []
    for index, shot in enumerate(visual_report.get("shots") or []):
        shot_id = str(shot.get("shot_id") or f"shot_{index + 1:04d}")
        metrics = []
        frame_errors = []
        for frame in (shot.get("sample_frames") or [])[:3]:
            try:
                metrics.append(_frame_metrics(frame))
            except Exception as exc:
                frame_errors.append(f"{Path(frame).name}: {type(exc).__name__}")
        if not metrics:
            rows.append({"shot_id": shot_id, "status": "unavailable",
                         "frame_count": 0, "issues": frame_errors or ["No readable sample frames."]})
            continue
        keys = ("brightness", "contrast", "saturation", "clipped_pixel_ratio", "sharpness_proxy")
        average = {key: round(statistics.mean(item[key] for item in metrics), 2) for key in keys}
        min_width = min(item["width"] for item in metrics)
        min_height = min(item["height"] for item in metrics)
        average.update({"min_width": min_width, "min_height": min_height})
        failure_issues, review_issues = [], []
        if min_width < 640 or min_height < 360:
            failure_issues.append("Sampled visual is below the recommended 640×360 source resolution.")
        if average["brightness"] < 12:
            failure_issues.append("Visual is severely underexposed.")
        elif average["brightness"] > 242:
            failure_issues.append("Visual is severely overexposed.")
        # A simple clipped-pixel percentage over-penalizes legitimate night footage,
        # archival film, letterboxed shots and intentional high-key/low-key scenes.
        # Treat clipping as a hard failure only when almost the entire frame is
        # clipped AND the image has virtually no tonal detail. Exposure extremes
        # are independently blocked by the brightness checks above.
        if average["clipped_pixel_ratio"] > 0.85 and average["contrast"] < 3.0:
            failure_issues.append(
                "Frame is almost entirely clipped and has negligible tonal detail; replace or re-render this shot."
            )
        if average["contrast"] < 8:
            review_issues.append("Visual has very low contrast; inspect readability and detail.")
        if average["sharpness_proxy"] < 1.2:
            review_issues.append("Visual appears unusually soft or blurred; inspect focus and source quality.")
        row_issues = failure_issues + review_issues
        rows.append({
            "shot_id": shot_id,
            "status": "fail" if failure_issues else ("review" if review_issues else "pass"),
            "frame_count": len(metrics), "metrics": average, "issues": row_issues,
            "frame_metrics": metrics,
        })
        for message in failure_issues:
            issues.append({"level": "error", "shot_id": shot_id, "message": message})
        for message in review_issues:
            issues.append({"level": "warning", "shot_id": shot_id, "message": message})

    measured = [row for row in rows if row.get("metrics")]
    medians = {}
    for key in ("brightness", "contrast", "saturation"):
        values = [row["metrics"][key] for row in measured]
        medians[key] = round(statistics.median(values), 2) if values else None

    # Relative outliers are review prompts, not automatic failures.
    for row in measured:
        row_issues = row.setdefault("issues", [])
        for key, threshold in (("brightness", 42), ("contrast", 45), ("saturation", 55)):
            median = medians.get(key)
            if median is not None and abs(row["metrics"][key] - median) > threshold:
                row_issues.append(
                    f"{key.capitalize()} differs substantially from the documentary median ({median})."
                )
        if row_issues and row["status"] == "pass":
            row["status"] = "review"
        for message in row_issues:
            if "differs substantially" in message:
                issues.append({"level": "warning", "shot_id": row["shot_id"], "message": message})

    summary = {
        "shot_count": len(rows),
        "measured_shot_count": len(measured),
        "pass_count": sum(row["status"] == "pass" for row in rows),
        "review_count": sum(row["status"] == "review" for row in rows),
        "fail_count": sum(row["status"] == "fail" for row in rows),
        "unavailable_count": sum(row["status"] == "unavailable" for row in rows),
        "documentary_medians": medians,
    }
    report = {
        "summary": summary, "shots": rows, "issues": issues,
        "status": "FAIL" if summary["fail_count"] else ("REVIEW" if summary["review_count"] or summary["unavailable_count"] else "PASS"),
        "notice": "Exposure, contrast, saturation and sharpness proxies are technical diagnostics only. Relative outliers need human review; these metrics do not establish semantic match, authenticity, factual accuracy or rights.",
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "visual_consistency_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def color_grade_filter(preset: str | None = None) -> str:
    """Return a restrained, consistent FFmpeg grade applied equally to every shot."""
    import os

    selected = (preset or os.getenv("DOCUMENTARY_COLOR_GRADE", "cinematic")).strip().lower()
    grades = {
        "neutral": "eq=contrast=1.0:brightness=0:saturation=1.0:gamma=1.0",
        "cinematic": (
            "eq=contrast=1.06:brightness=-0.015:saturation=0.94:gamma=1.02,"
            "colorbalance=rs=0.015:gs=0.003:bs=-0.015:rh=0.025:bh=-0.02"
        ),
        "warm": (
            "eq=contrast=1.05:brightness=-0.01:saturation=0.96:gamma=1.02,"
            "colorbalance=rs=0.035:gs=0.008:bs=-0.025:rh=0.04:bh=-0.025"
        ),
        "cool": (
            "eq=contrast=1.05:brightness=-0.01:saturation=0.92:gamma=1.02,"
            "colorbalance=rs=-0.015:gs=0.002:bs=0.025:rh=-0.02:bh=0.035"
        ),
        "archive": (
            "eq=contrast=1.04:brightness=-0.01:saturation=0.72:gamma=1.03,"
            "colorbalance=rs=0.025:gs=0.008:bs=-0.03"
        ),
    }
    return grades.get(selected, grades["cinematic"])
