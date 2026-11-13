import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from core.footage_sources import register_indian_archive_asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to the video file obtained from the archive")
    parser.add_argument("--provider", required=True, choices=["NCAA", "Prasar Bharati Archives"])
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--licence-reference", required=True, help="Permission email, licence ID or document reference")
    parser.add_argument("--usage-scope", required=True, help="Must explicitly cover commercial/monetized YouTube use")
    parser.add_argument("--creator", default="")
    parser.add_argument("--attribution", default="")
    parser.add_argument("--licence-expiry", default="")
    args = parser.parse_args()
    target, sidecar = register_indian_archive_asset(
        args.file, provider=args.provider, title=args.title,
        source_url=args.source_url, licence_reference=args.licence_reference,
        usage_scope=args.usage_scope, creator=args.creator,
        attribution=args.attribution, licence_expiry=args.licence_expiry,
    )
    print(f"Registered Indian archive clip: {target}")
    print(f"Rights/provenance sidecar: {sidecar}")


if __name__ == "__main__":
    main()
