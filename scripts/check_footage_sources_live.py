"""Live, read-only footage discovery validation for a supplied documentary topic.

Checks archive APIs plus the stock-video providers used by the production
renderer. A discovery candidate is not a downloaded or publication-cleared clip.
"""
import argparse
import json
from datetime import date

from core import footage_sources as fs
from core import images

DEFAULT_QUERY = "India disappearing villages rural migration abandoned villages"
DEFAULT_QUERY_VARIANTS = (
    "Indian village",
    "rural India",
    "Indian countryside",
    "village life India",
    "Indian farming village",
    "rural village landscape India",
    "India village rural life",
    "Indian village landscape",
    "rural India agriculture",
    "India village migration",
)


def _coverr(query):
    results = []
    for hit in images._search_coverr(query):
        item = images._coverr_candidate(hit, query)
        if item:
            results.append(item)
    return results


def _pixabay(query):
    results = []
    for hit in images._search_pixabay(query):
        if not isinstance(hit, dict) or (hit.get("duration") or 0) < 3:
            continue
        variants = hit.get("videos") or {}
        best = max(
            variants.values(),
            key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
            default={},
        )
        width, height = int(best.get("width") or 0), int(best.get("height") or 0)
        if width < 720 or height < 400 or not best.get("url"):
            continue
        tags = str(hit.get("tags") or "")
        results.append((best["url"], {
            "provider": "Pixabay",
            "asset_id": str(hit.get("id") or ""),
            "title": tags,
            "creator": str(hit.get("user") or ""),
            "page_url": str(hit.get("pageURL") or ""),
            "licence": "Pixabay Content License",
            "licence_url": "https://pixabay.com/service/license-summary/",
            "query": query,
            "date_accessed": date.today().isoformat(),
            "width": width,
            "height": height,
            "licence_status": "Pixabay Content License identified; check restrictions and item context",
        }))
    return results


def _query_variants(query):
    values = [query]
    normalized = query.lower()
    if "india" in normalized or "village" in normalized:
        values.extend(DEFAULT_QUERY_VARIANTS)
    else:
        values.extend([query + " video", query + " documentary"])
    # Keep stable order and avoid redundant requests.
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:10]


def run_checks(query, min_total=1, providers=None):
    providers = providers or [
        ("NASA", fs._nasa),
        ("Library of Congress", fs._loc),
        ("Wikimedia Commons", fs._wikimedia),
        ("Internet Archive", fs._internet_archive),
        ("NARA", fs._nara),
        ("DVIDS", fs._dvids),
        ("Europeana CC", fs._europeana_cc),
        ("Coverr", _coverr),
        ("Pixabay", _pixabay),
    ]
    report = []
    total = 0
    queries = _query_variants(query)
    for name, call in providers:
        provider_count = 0
        errors = []
        for candidate_query in queries:
            try:
                provider_count += len(call(candidate_query) or [])
            except Exception as exc:
                errors.append(str(exc)[:500])
        total += provider_count
        if provider_count:
            status = "ok"
        elif errors:
            status = "error"
        else:
            status = "no_eligible_results"
        entry = {
            "provider": name,
            "status": status,
            "eligible_candidates": provider_count,
            "queries_checked": queries,
        }
        if errors:
            entry["errors"] = errors[:3]
        report.append(entry)
    result = {
        "query": query,
        "queries_checked": queries,
        "minimum_total_candidates": min_total,
        "total_eligible_candidates": total,
        "providers": report,
        "status": "passed" if total >= min_total else "failed",
        "important": (
            "Discovery candidates are not downloaded or publication-cleared. "
            "Coverr candidates remain subject to exact-item licence review. "
            "Each selected asset still requires file-level rights and visual review."
        ),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument("--min-total", type=int, default=1)
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
