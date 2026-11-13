"""Draft a news Short script from a human-approved packet.

Exit codes (so the workflow and the operator can tell failures apart):

* 0  draft written
* 2  packet rejected (not approved, incomplete, or the source-verification gate failed)
* 4  credentials (GEMINI_API_KEY missing or rejected by Google)
* 5  provider unavailable (every configured model failed after bounded retries)
* 6  the model answered, but no draft passed validation after three attempts
* 7  invalid model configuration (GEMINI_MODEL / GEMINI_FALLBACK_MODELS)
* 1  unexpected error

On failure a non-sensitive ``draft_failure.json`` is written next to the output
so the workflow can report the real reason instead of "check the logs".
"""
import sys as _sys
from pathlib import Path as _Path

if __package__ in (None, ""):  # run as "python scripts/<name>.py": make the repo importable
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import os
import sys
from pathlib import Path

from core import gemini
from core.news_shorts_writer import generate_reviewed_shorts

EXIT_PACKET, EXIT_CREDENTIALS, EXIT_PROVIDER, EXIT_VALIDATION, EXIT_CONFIG = 2, 4, 5, 6, 7

ADVICE = {
    "packet_rejected": "Correct the packet with inspected, eligible evidence and approve it again. "
                       "The source-verification gate must not be bypassed.",
    "credentials": "Create a new key in Google AI Studio and update the GEMINI_API_KEY repository secret.",
    "provider_unavailable": "Gemini quota or capacity was exhausted on every configured model. "
                            "Comment /approve-script again later, or add models to the "
                            "GEMINI_FALLBACK_MODELS repository variable.",
    "validation_failed": "The model answered but its drafts broke the script rules listed below. "
                         "Comment /approve-script to try again; if it repeats, simplify the packet facts.",
    "configuration": "Fix the GEMINI_MODEL / GEMINI_FALLBACK_MODELS repository variables "
                     "(comma-separated plain model ids).",
    "unexpected": "Open the workflow log for the traceback.",
}


def classify_failure(exc: BaseException) -> tuple[str, int]:
    """Map an exception from the writer to (category, exit code)."""
    message = str(exc)
    if isinstance(exc, gemini.GeminiConfigError):
        return "configuration", EXIT_CONFIG
    if isinstance(exc, gemini.GeminiCredentialError) or "GEMINI_API_KEY is required" in message:
        return "credentials", EXIT_CREDENTIALS
    if isinstance(exc, ValueError) and message.startswith("Generated script failed validation"):
        return "validation_failed", EXIT_VALIDATION
    if isinstance(exc, ValueError) and message.startswith(("Generated claim has unknown", "Scene has unknown")):
        return "validation_failed", EXIT_VALIDATION
    if isinstance(exc, (ValueError, FileNotFoundError, json.JSONDecodeError)):
        return "packet_rejected", EXIT_PACKET
    if isinstance(exc, RuntimeError) and "Gemini" in message:
        return "provider_unavailable", EXIT_PROVIDER
    return "unexpected", 1


def _redact(text: str) -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    text = str(text)
    if key and len(key) >= 6:
        text = text.replace(key, "***")
    try:
        from core.llm import redact
        text = redact(text)
    except Exception:
        pass
    return text


def write_failure(output: Path, exc: BaseException) -> dict:
    category, code = classify_failure(exc)
    usage = gemini.last_usage("news_script")
    report = {
        "status": "failed",
        "category": category,
        "exit_code": code,
        "reason": _redact(exc)[:1500],
        "advice": ADVICE[category],
        "attempted_models": usage.get("attempted_models") or [],
        "model_failures": [_redact(item)[:300] for item in (usage.get("failures") or [])][:8],
    }
    path = Path(output).with_name("draft_failure.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return report


def _summarise(lines: list[str]) -> None:
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
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
    args = parser.parse_args(argv)
    Path(args.output).with_name("draft_failure.json").unlink(missing_ok=True)
    try:
        result = generate_reviewed_shorts(args.reviewed_packet, args.output, language=args.language)
    except Exception as exc:  # reported, classified, and turned into an exit code
        report = write_failure(Path(args.output), exc)
        print(f"::error title=News script draft failed ({report['category']})::{report['reason'][:400]}")
        print(f"Draft failed [{report['category']}]: {report['reason']}", file=sys.stderr)
        print("What to do: " + report["advice"], file=sys.stderr)
        _summarise([
            "## News script draft: FAILED",
            "",
            f"- **Reason category:** `{report['category']}`",
            f"- **Detail:** {report['reason'][:600]}",
            f"- **Models tried:** {', '.join(report['attempted_models']) or 'none (failed before any request)'}",
            f"- **What to do:** {report['advice']}",
            "- **Upload status:** nothing was rendered or uploaded",
        ])
        if report["category"] == "unexpected":
            raise
        return report["exit_code"]
    generation = result.get("generation") or {}
    print(
        "Draft saved:", args.output,
        "| language:", result["language"],
        "| scenes:", len(result["scenes"]),
        "| words:", result["word_count"],
        "| model:", generation.get("model"),
    )
    print("Human script, factual and visual review remains required; this command does not render or upload.")
    _summarise([
        "## News script draft: READY FOR HUMAN REVIEW",
        "",
        f"- **Language:** {result['language']}",
        f"- **Scenes / words:** {len(result['scenes'])} / {result['word_count']}",
        f"- **Model used:** {generation.get('model')}"
        + (" (fallback)" if generation.get("fallback_used") else ""),
        "- **Source verification:** passed",
        "- **Upload status:** nothing was rendered or uploaded",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
