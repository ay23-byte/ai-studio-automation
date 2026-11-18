#!/usr/bin/env python3
"""Decide what the documentary workflow builds, and whether it builds at all.

The workflow starts in two ways:

* **Run workflow** (workflow_dispatch): the form's inputs are used.
* **A weekly schedule** selects a topic from the curated rotation.
* **A pull request that changes `.github/documentary-production-request.json`**, when the
  newest commit message contains `[documentary-run]`. This is a production *test*: it runs
  the same steps with the repository's real providers. It uploads nothing. Whether such a
  build may later be released is decided by `scripts/verify_source_run.py` (only a pull
  request from this repository that targets the release branch) and by the review gates.
  Without the marker nothing is built, so ordinary pushes to the pull request cost nothing.

Every value is checked against the same choices the form offers, and written to
`$GITHUB_OUTPUT` for the build job.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# A script launched as ``python scripts/resolve_documentary_request.py`` gets
# scripts/ (not the repository root) on sys.path. Add the root before importing
# the shared topic queue so both direct execution and package imports work.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.documentary_topic_queue import scheduled_topic

REQUEST_FILE = ".github/documentary-production-request.json"
MARKER = "[documentary-run]"
CHOICES = {
    "minutes": ["18", "20", "22", "25", "28", "30"],
    "language": ["hinglish", "hindi", "english"],
    "channel_profile": ["beyond_science", "standard"],
    "voice_gender": ["male", "female"],
    "presenter_mode": ["talking_avatar_auto", "talking_avatar", "sadtalker", "still_cpu", "off"],
    "visual_mode": ["video_first", "image_first"],
}
DEFAULTS = {
    "category": "auto", "semantic_review": "false", "minutes": "22", "language": "hinglish",
    "channel_profile": "beyond_science", "voice_gender": "male",
    "presenter_mode": "talking_avatar_auto", "visual_mode": "video_first",
}
FIELDS = ["topic", "category", "semantic_review", "minutes", "language", "channel_profile",
          "voice_gender", "presenter_mode", "visual_mode"]


class RequestError(ValueError):
    """The build request is missing or holds a value the workflow does not offer."""


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return re.sub(r"[\r\n\t]+", " ", str(value if value is not None else "")).strip()


def normalise(raw: dict) -> dict[str, str]:
    """Validated parameters, as the strings the workflow form would have supplied."""
    values = {}
    for field in FIELDS:
        given = _text(raw.get(field))
        values[field] = given or DEFAULTS.get(field, "")
    if not values["topic"]:
        raise RequestError("A topic is required.")
    if len(values["topic"]) > 200:
        raise RequestError("The topic is longer than 200 characters.")
    if not re.fullmatch(r"[A-Za-z0-9 _&/+-]{1,40}", values["category"]):
        raise RequestError("The category may contain letters, digits, spaces and _ & / + - only (40 at most).")
    values["semantic_review"] = "true" if values["semantic_review"].lower() in {"true", "1", "yes"} else "false"
    for field, allowed in CHOICES.items():
        if values[field] not in allowed:
            raise RequestError(f"{field} must be one of {', '.join(allowed)} (got {values[field]!r}).")
    return values


def resolve(event: str, inputs: dict, request_path: Path, head_message: str, same_repository: bool) -> dict[str, str]:
    """{"run": "true"|"false", "reason": ..., plus the parameters when run is true}."""
    if event == "schedule":
        return {
            "run": "true",
            "reason": "Weekly scheduled documentary build; topic selected from the curated rotation.",
            **normalise({
                "topic": scheduled_topic(),
                "category": "auto",
                "semantic_review": "true",
                "minutes": "22",
                "language": "hinglish",
                "channel_profile": "beyond_science",
                "voice_gender": "male",
                "presenter_mode": "talking_avatar_auto",
                "visual_mode": "video_first",
            }),
        }
    if event == "workflow_dispatch":
        return {"run": "true", "reason": "Started from the Run workflow form.", **normalise(inputs)}
    if event != "pull_request":
        return {"run": "false", "reason": f"Event {event!r} does not start a documentary build."}
    if not same_repository:
        return {"run": "false", "reason": "Pull requests from forks never start a build."}
    if MARKER not in (head_message or ""):
        return {"run": "false", "reason": f"The newest commit message does not contain {MARKER}; nothing is built."}
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RequestError(f"{REQUEST_FILE} could not be read: {exc}") from exc
    except ValueError as exc:
        raise RequestError(f"{REQUEST_FILE} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RequestError(f"{REQUEST_FILE} must hold one JSON object.")
    return {"run": "true", "reason": f"Production documentary run requested by {REQUEST_FILE}; eligible for machine-gated PRIVATE release after a successful build.",
            **normalise(raw)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", default=REQUEST_FILE)
    args = parser.parse_args(argv)
    inputs = {field: os.environ.get(f"INPUT_{field.upper()}", "") for field in FIELDS}
    try:
        result = resolve(os.environ.get("EVENT_NAME", ""), inputs, Path(args.request),
                         os.environ.get("HEAD_COMMIT_MESSAGE", ""),
                         os.environ.get("SAME_REPOSITORY", "false").lower() == "true")
    except RequestError as exc:
        print(f"::error title=Documentary request::{exc}")
        return 1
    print(result["reason"])
    for key, value in result.items():
        if key != "reason":
            print(f"  {key}: {value}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={_text(value)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
