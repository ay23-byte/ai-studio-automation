"""Register manually downloaded Mixkit clips with exact licence provenance.

Mixkit has no public API and its terms forbid automated downloading, so this
project never fetches from Mixkit. A person downloads each clip, checks that the
clip's own page shows the Free License, and registers it here. Every file is
inspected with ffprobe before it is accepted.

One clip:
    python -m scripts.register_mixkit_asset clip.mp4 --title "Waves on a beach" \
        --source-url https://mixkit.co/free-stock-video/waves-on-a-beach-1234/ \
        --licence "Mixkit Free License" --confirm-free-license

Several clips, from a JSON manifest (a list of objects with "file", "title",
"source_url", "licence" and optional "creator", "attribution", "tags"; file
paths are relative to the manifest):
    python -m scripts.register_mixkit_asset --manifest clips/manifest.json --confirm-free-license
"""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json

from core.footage_sources import import_mixkit_manifest, register_mixkit_asset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="Path to the manually downloaded Mixkit video")
    parser.add_argument("--manifest", help="JSON manifest listing several clips to import")
    parser.add_argument("--title", help="Exact clip title on Mixkit")
    parser.add_argument("--source-url", help="Exact Mixkit clip page URL")
    parser.add_argument("--licence", choices=["Mixkit Free License", "Mixkit Restricted License"])
    parser.add_argument("--creator", default="")
    parser.add_argument("--attribution", default="")
    parser.add_argument("--tags", default="", help="Comma-separated words describing the clip, used for scene matching")
    parser.add_argument("--confirm-free-license", action="store_true",
                        help="Confirm that each asset page displays the Free License")
    args = parser.parse_args(argv)
    if not args.confirm_free_license:
        parser.error("You must inspect the asset page and pass --confirm-free-license.")
    if args.manifest:
        if args.file:
            parser.error("Give either a file or --manifest, not both.")
        result = import_mixkit_manifest(args.manifest, confirm_free_license=True)
        for item in result["imported"]:
            print(f"Registered Mixkit clip: {item.get('stored_as')}")
        for item in result["rejected"]:
            print(f"Rejected entry {item.get('entry')} ({item.get('file') or 'no file'}): {item.get('reason')}")
        print(json.dumps({"entries": result["entries"], "imported": len(result["imported"]),
                          "rejected": len(result["rejected"])}))
        return 0 if result["imported"] and not result["rejected"] else 1
    missing = [flag for flag, value in (("file", args.file), ("--title", args.title),
                                        ("--source-url", args.source_url), ("--licence", args.licence)) if not value]
    if missing:
        parser.error("Missing: " + ", ".join(missing))
    try:
        target, sidecar = register_mixkit_asset(
            args.file, title=args.title, source_url=args.source_url,
            licence=args.licence, creator=args.creator, attribution=args.attribution,
            **({"tags": args.tags} if args.tags else {}),
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Not registered: {exc}")
        return 1
    print(f"Registered Mixkit clip: {target}")
    print(f"Provenance sidecar: {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
