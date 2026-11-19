"""Opt-in command line for the isolated v2 production stack.

No existing workflow calls this module. It adds no schedule and never uploads
unless PV2_UPLOAD=1 plus every critical QA check passes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .artifacts import read_json
from .config import V2Config
from .deps import check_dependencies
from .pipeline import dossier_from_dict, run_pipeline
from .research import validate_dossier


DEMO_DOSSIERS: dict[str, dict] = {
    "documentary": {
        "topic": "Demonstration: how a dated source panel supports a documentary claim",
        "content_type": "documentary",
        "thesis": "A dated, attributable source is the basis for every visual claim.",
        "demo_mode": True,
        "sources": [
            {"id": "demo-doc-1", "title": "Synthetic demonstration record A",
             "publisher": "Engineering Physics Demo", "url": "https://example.invalid/doc/a",
             "published_at": "2026-01-01", "reliability": "demo"},
            {"id": "demo-doc-2", "title": "Synthetic demonstration record B",
             "publisher": "Engineering Physics Demo", "url": "https://example.invalid/doc/b",
             "published_at": "2026-01-02", "reliability": "demo"},
            {"id": "demo-doc-3", "title": "Synthetic demonstration record C",
             "publisher": "Engineering Physics Demo", "url": "https://example.invalid/doc/c",
             "published_at": "2026-01-03", "reliability": "demo"},
        ],
        "claims": [
            {"id": "doc-c1", "text": "The demonstration timeline has three dated entries.",
             "status": "established", "source_ids": ["demo-doc-1"],
             "evidence": "synthetic demo record", "demonstration_only": True},
            {"id": "doc-c2", "text": "Each visual panel is labelled with its evidence panel number.",
             "status": "established", "source_ids": ["demo-doc-2"],
             "evidence": "synthetic demo record", "demonstration_only": True},
            {"id": "doc-c3", "text": "A claim without a source is refused by the research gate.",
             "status": "established", "source_ids": ["demo-doc-3"],
             "evidence": "synthetic demo record", "demonstration_only": True},
        ],
    },
    "news_short": {
        "topic": "Demonstration: source-attributed current-affairs short",
        "content_type": "news_short",
        "thesis": "Every important statement in a news short carries an attribution.",
        "demo_mode": True,
        "sources": [
            {"id": "demo-news-1", "title": "Synthetic demonstration bulletin",
             "publisher": "Engineering Physics Demo", "url": "https://example.invalid/news/1",
             "published_at": "2026-11-19", "reliability": "demo"},
            {"id": "demo-news-2", "title": "Synthetic demonstration corroboration",
             "publisher": "Engineering Physics Demo Wire", "url": "https://example.invalid/news/2",
             "published_at": "2026-11-19", "reliability": "demo"},
        ],
        "claims": [
            {"id": "news-c1", "text": "The demonstration event was reported at a stated time.",
             "status": "established", "source_ids": ["demo-news-1"],
             "evidence": "synthetic demo bulletin", "demonstration_only": True},
            {"id": "news-c2", "text": "A second demonstration source confirms the same detail.",
             "status": "established", "source_ids": ["demo-news-2"],
             "evidence": "synthetic demo wire", "demonstration_only": True},
        ],
    },
    "physics_short": {
        "topic": "Demonstration: one physics concept, one visual",
        "content_type": "physics_short",
        "thesis": "A vector diagram can make one concept understandable.",
        "demo_mode": True,
        "sources": [
            {"id": "demo-phy-1", "title": "Synthetic demonstration explanation",
             "publisher": "Engineering Physics Demo", "url": "https://example.invalid/physics/1",
             "published_at": "2026-01-01", "reliability": "demo"},
        ],
        "claims": [
            {"id": "phy-c1", "text": "A force is represented by a labelled vector.",
             "status": "established", "source_ids": ["demo-phy-1"],
             "evidence": "synthetic demo explanation", "demonstration_only": True},
            {"id": "phy-c2", "text": "The diagram states the assumption before it explains the result.",
             "status": "established", "source_ids": ["demo-phy-1"],
             "evidence": "synthetic demo explanation", "demonstration_only": True},
        ],
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Engineering Physics production v2 (opt-in)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check free dependencies and safety configuration")
    p_research = sub.add_parser("research", help="validate a research dossier JSON")
    p_research.add_argument("dossier", type=Path)
    p_render = sub.add_parser("render", help="render one pipeline from a dossier JSON")
    p_render.add_argument("content_type", choices=sorted(DEMO_DOSSIERS))
    p_render.add_argument("dossier", type=Path)
    p_demo = sub.add_parser("demo", help="render all three labelled offline demonstration videos")
    p_demo.add_argument("--content-type", choices=sorted(DEMO_DOSSIERS))
    p_check = sub.add_parser("publish-dry-run", help="show the private-upload decision for a run")
    p_check.add_argument("run_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = V2Config.from_env()
    try:
        config.assert_safe()
    except ValueError as exc:
        print(f"SAFETY ERROR: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        print(json.dumps(check_dependencies(config).as_dict(), indent=2))
        return 0
    if args.command == "research":
        dossier = dossier_from_dict(read_json(args.dossier, {}) or {})
        assessment = validate_dossier(dossier, min_sources=config.rules_for(dossier.content_type).min_sources)
        print(json.dumps(assessment.as_dict(), indent=2))
        return 0 if assessment.accepted else 1
    if args.command == "render":
        dossier = dossier_from_dict(read_json(args.dossier, {}) or {})
        result = run_pipeline(args.content_type, dossier.topic, dossier, config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["video_ready"] else 1
    if args.command == "demo":
        selected = ([args.content_type] if args.content_type else sorted(DEMO_DOSSIERS))
        results = []
        for content_type in selected:
            data = DEMO_DOSSIERS[content_type]
            dossier = dossier_from_dict(data)
            results.append(run_pipeline(content_type, data["topic"], dossier, config))
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0 if all(r["video_ready"] for r in results) else 1
    if args.command == "publish-dry-run":
        decision = read_json(args.run_dir / "quality_decision.json", {}) or {}
        upload = read_json(args.run_dir / "upload_result.json", {}) or {}
        print(json.dumps({"quality_decision": decision, "upload_result": upload,
                          "privacy": "private", "public_release": False}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
