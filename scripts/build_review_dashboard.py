"""Build the offline review dashboard for a shared review package.

    python scripts/build_review_dashboard.py <package_dir>

Writes ``review_dashboard.html`` (interactive, self-contained, no network) and
``review_decisions_template.json``.  See ``core/review_dashboard.py``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.review_contract import ContractError
from core.review_dashboard import build_dashboard  # noqa: F401  (re-exported)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package_dir")
    args = parser.parse_args(argv)
    try:
        print(build_dashboard(args.package_dir))
    except ContractError as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
