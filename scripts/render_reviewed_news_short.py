import argparse
import json
from core.news_short_production import render_review_copy


def main():
    parser = argparse.ArgumentParser(description="Render a reviewed news Short for human approval; never uploads.")
    parser.add_argument("script", help="Reviewed script JSON")
    parser.add_argument("approval", help="Separate production approval JSON")
    parser.add_argument("--output-dir", default="output/daily_news/rendered")
    args = parser.parse_args()
    result = render_review_copy(args.script, args.approval, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Review copy only: no YouTube upload or publication was attempted.")


if __name__ == "__main__":
    main()
