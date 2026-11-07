"""Live, read-only smoke checks for configured footage discovery APIs."""
import json
from core import footage_sources as fs


def main():
    checks = [
        ("NASA", lambda: fs._nasa("Apollo mission")),
        ("Library of Congress", lambda: fs._loc("Apollo mission")),
        ("Wikimedia Commons", lambda: fs._wikimedia("Apollo mission")),
        ("Internet Archive", lambda: fs._internet_archive("Apollo mission")),
        ("NARA", lambda: fs._nara("Apollo mission")),
        ("DVIDS", lambda: fs._dvids("Apollo mission")),
        ("Europeana CC", lambda: fs._europeana_cc("Apollo mission")),
    ]
    report = []
    for name, call in checks:
        try:
            results = call()
            report.append({"provider": name, "status": "ok" if results else "no_eligible_results",
                           "eligible_candidates": len(results)})
        except Exception as exc:
            report.append({"provider": name, "status": "error", "error": str(exc)[:500]})
    print(json.dumps(report, indent=2))
    failures = [x for x in report if x["status"] == "error"]
    if failures:
        raise SystemExit(f"{len(failures)} provider smoke checks raised errors")


if __name__ == "__main__":
    main()
