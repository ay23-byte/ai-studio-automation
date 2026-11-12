import argparse
from core.news_shorts_writer import generate_reviewed_shorts


def main():
    parser = argparse.ArgumentParser(
        description="Draft a Short from a human-approved source packet."
    )
    parser.add_argument("reviewed_packet")
    parser.add_argument("--output", default="output/daily_news/shorts_script_draft.json")
    parser.add_argument(
        "--language",
        choices=("english", "hinglish", "hindi"),
        default=None,
        help="Narration/caption language; defaults to the packet language or Hinglish.",
    )
    args = parser.parse_args()
    result = generate_reviewed_shorts(args.reviewed_packet, args.output, language=args.language)
    print(
        "Draft saved:", args.output,
        "| language:", result["language"],
        "| scenes:", len(result["scenes"]),
        "| words:", result["word_count"],
    )
    print("Human script, factual and visual review remains required; this command does not render or upload.")


if __name__ == "__main__":
    main()
