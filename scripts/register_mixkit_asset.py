"""Register a manually downloaded Mixkit clip with exact licence provenance.

Mixkit automated scraping/bulk downloading is intentionally not implemented.
The operator must inspect the asset page and confirm its Free License.
"""
import argparse
from core.footage_sources import register_mixkit_asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="Path to the manually downloaded Mixkit video")
    parser.add_argument("--title", required=True, help="Exact clip title on Mixkit")
    parser.add_argument("--source-url", required=True, help="Exact Mixkit clip page URL")
    parser.add_argument("--licence", required=True, choices=["Mixkit Free License", "Mixkit Restricted License"])
    parser.add_argument("--creator", default="")
    parser.add_argument("--attribution", default="")
    parser.add_argument("--confirm-free-license", action="store_true",
                        help="Confirm that this exact asset page displays the Free License")
    args = parser.parse_args()
    if not args.confirm_free_license:
        parser.error("You must inspect the asset page and pass --confirm-free-license.")
    target, sidecar = register_mixkit_asset(
        args.file, title=args.title, source_url=args.source_url,
        licence=args.licence, creator=args.creator, attribution=args.attribution,
    )
    print(f"Registered Mixkit clip: {target}")
    print(f"Provenance sidecar: {sidecar}")


if __name__ == "__main__":
    main()
