"""Live, read-only footage discovery check for a supplied documentary topic.

Searches the archive APIs and stock libraries the renderer uses and counts the
results that pass the licence policy. Nothing is downloaded: a passing result
shows that search works, not that files download and play. The check that
downloads and probes one file per provider is scripts/verify_footage_providers.py.
"""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json

from core import footage_report
from core import footage_sources as fs
from core import images
from core.footage_licence import classify_licence, provenance_gaps

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


def _query_variants(query):
    values = [query]
    normalized = query.lower()
    if "india" in normalized or "village" in normalized:
        values.extend(DEFAULT_QUERY_VARIANTS)
    elif "north korea" in normalized or "north korean" in normalized:
        # Long editorial titles often perform poorly as stock-library search
        # phrases. Search concise, topic-grounded geographic terms as well.
        values.extend([
            "North Korea", "Pyongyang", "Korean peninsula",
            "Korean DMZ", "North Korean landscape", "Korea border",
        ])
        values.extend([query + " video", query + " documentary"])
    else:
        values.extend([query + " video", query + " documentary"])
    # Keep stable order and avoid redundant requests.
    # Bound the number of API requests per provider. Broad discovery is useful,
    # but ten variants across every archive causes avoidable rate limiting.
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:8]


def _default_providers():
    """Every automatic source the renderer uses, as ``(name, search, status)``."""
    providers = fs.archive_providers("space nasa") + images.stock_providers("")
    return [(provider.name, provider.search, provider.status) for provider in providers]


def _licensed(items):
    """Count results the licence policy would let the renderer download."""
    count = 0
    for item in items or []:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], dict):
            if not classify_licence(item[1])["publishable"] or provenance_gaps(item[1]):
                continue
        count += 1
    return count


def run_checks(query, min_total=1, providers=None):
    """Search each provider and count licensed discovery results. Nothing is downloaded.

    A provider that returns results here has a working search. It is not proof
    that its files download and play; scripts/verify_footage_providers.py does that.
    """
    fs.reset_run_state()
    run = footage_report.begin_run(f"discovery check: {query}"[:120])
    report = []
    total = 0
    queries = _query_variants(query)
    for entry in providers or _default_providers():
        name, call = entry[0], entry[1]
        status_check = entry[2] if len(entry) > 2 else None
        provider_count = 0
        errors = []
        enabled, auth, reason = status_check() if status_check else (True, "not_required", "")
        run.set(name, enabled=bool(enabled), auth_status=auth)
        if enabled:
            with footage_report.provider_scope(name, run):
                for candidate_query in queries:
                    try:
                        provider_count += _licensed(call(candidate_query))
                    except Exception as exc:
                        errors.append(footage_report.redact(str(exc))[:500])
        total += provider_count
        facts = run.provider(name)
        failure = footage_report.redact(facts.get("failure_reason") or "")
        if provider_count:
            status = "ok"
        elif not enabled:
            status = "not_configured" if auth == "missing_key" else "disabled"
        elif errors:
            status = "error"
        elif facts.get("auth_status") == "rejected":
            status = "auth_failed"
        elif facts.get("search_status") == "rate_limited":
            status = "rate_limited"
        elif facts.get("search_status") in ("network_error", "timeout", "server_error", "blocked"):
            status = "unreachable"
        else:
            status = "no_eligible_results"
        entry_report = {
            "provider": name,
            "status": status,
            "eligible_candidates": provider_count,
            "queries_checked": queries,
        }
        if errors:
            entry_report["errors"] = errors[:3]
        if not enabled and reason:
            entry_report["reason"] = footage_report.redact(reason)
        elif failure and not provider_count:
            entry_report["reason"] = failure
        if facts.get("http_status_counts"):
            entry_report["http_status_counts"] = dict(facts["http_status_counts"])
        report.append(entry_report)
    footage_report.end_run()
    result = {
        "query": query,
        "queries_checked": queries,
        "minimum_total_candidates": min_total,
        "total_eligible_candidates": total,
        "providers": report,
        "providers_with_results": [item["provider"] for item in report if item["status"] == "ok"],
        "status": "passed" if total >= min_total else "failed",
        "important": (
            "This is a search check only: nothing was downloaded or played, so no provider is proven "
            "to deliver usable files by this result. Run scripts/verify_footage_providers.py for that. "
            "Each selected asset still requires file-level rights and visual review."
        ),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument("--min-total", type=int, default=1)
    parser.add_argument("--diagnostic-only", action="store_true", help="Report title-level provider availability without blocking scene-specific rendering.")
    args = parser.parse_args()
    if args.min_total < 0 or (args.min_total == 0 and not args.diagnostic_only):
        parser.error("--min-total must be positive unless --diagnostic-only is set")
    result = run_checks(args.query, args.min_total)
    if args.diagnostic_only and result["status"] != "passed":
        result["status"] = "diagnostic_warning"
        result["note"] = "Topic-title discovery is diagnostic only; each rendered scene still needs its own valid, relevant visual."
    print(json.dumps(result, indent=2))
    if result["status"] == "failed":
        raise SystemExit(
            f"Footage discovery produced {result['total_eligible_candidates']} "
            f"eligible candidates; {args.min_total} required."
        )


if __name__ == "__main__":
    main()
