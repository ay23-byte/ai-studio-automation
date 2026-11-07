"""Live, read-only footage discovery validation for a supplied documentary topic.

A provider with zero eligible results is reported, not silently treated as a
successful provider check. The overall command fails when the combined result
set cannot meet the requested minimum.
"""
import argparse
import json

from core import footage_sources as fs

DEFAULT_QUERY = "India disappearing villages rural migration abandoned villages"


def run_checks(query, min_total=1, providers=None):
    providers = providers or [
        ("NASA", fs._nasa),
        ("Library of Congress", fs._loc),
        ("Wikimedia Commons", fs._wikimedia),
        ("Internet Archive", fs._internet_archive),
        ("NARA", fs._nara),
        ("DVIDS", fs._dvids),
        ("Europeana CC", fs._europeana_cc),
    ]
    report = []
    total = 0
    for name, call in providers:
        try:
            results = call(query) or []
            count = len(results)
            total += count
            report.append({
                "provider": name,
                "status": "ok" if count else "no_eligible_results",
                "eligible_candidates": count,
            })
        except Exception as exc:
            report.append({
                "provider": name, "status": "error",
                "eligible_candidates": 0, "error": str(exc)[:500],
            })
    result = {
        "query": query,
        "minimum_total_candidates": min_total,
        "total_eligible_candidates": total,
        "providers": report,
        "status": "passed" if total >= min_total else "failed",
        "important": (
            "Discovery candidates are not downloaded or publication-cleared. "
            "Each selected asset still requires file-level rights and visual review."
        ),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "query", nargs="?", default=DEFAULT_QUERY,
        help="Topic-specific documentary search query",
    )
    parser.add_argument(
        "--min-total", type=int, default=1,
        help="Minimum combined eligible candidates required for success (default: 1)",
    )
    args = parser.parse_args()
    if args.min_total < 1:
        parser.error("--min-total must be at least 1")
    result = run_checks(args.query, args.min_total)
    print(json.dumps(result, indent=2))
    if result["status"] != "passed":
        raise SystemExit(
            f"Footage discovery produced {result['total_eligible_candidates']} "
            f"eligible candidates; {args.min_total} required."
        )


if __name__ == "__main__":
    main()
