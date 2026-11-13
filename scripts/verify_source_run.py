"""Check that a build run is a legitimate source for a private release.

    python scripts/verify_source_run.py --run-id 123 --mode shortform --ref main

A release may only use the artifact of a run that (1) belongs to this
repository, (2) is one of the review-only build workflows for that mode,
(3) completed successfully, and (4) ran on the same branch as the release.
Reads the run through the GitHub API with ``GITHUB_TOKEN`` (``actions: read``).
Exit codes: 0 accepted, 2 refused, 1 the API could not be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

#: Review-only build workflows whose artifacts may be released, per mode.
BUILD_WORKFLOWS = {
    "longform": {".github/workflows/make_longform.yml"},
    "shortform": {".github/workflows/make_shorts.yml", ".github/workflows/standard-shorts-review.yml"},
    "daily_news": {".github/workflows/daily-news-auto-short.yml",
                   ".github/workflows/news-short-review-render.yml",
                   ".github/workflows/daily-news-short-render.yml",
                   ".github/workflows/daily-news-short-artifact-render.yml"},
}
ALLOWED_EVENTS = {"workflow_dispatch", "schedule", "issues", "issue_comment"}


def fetch_run(repository: str, run_id: str, token: str, api_url: str = "https://api.github.com") -> dict:
    request = urllib.request.Request(
        f"{api_url}/repos/{repository}/actions/runs/{run_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-studio-release-check"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https API host)
        return json.loads(response.read().decode("utf-8"))


def problems_with(run: dict, *, repository: str, mode: str, ref: str | None, run_id: str) -> list[str]:
    """Every reason this run cannot be the source of a release."""
    if mode not in BUILD_WORKFLOWS:
        return [f"Unknown mode {mode!r}."]
    problems = []
    if str(run.get("id")) != str(run_id):
        problems.append("The API returned a different run than the one requested.")
    actual_repo = str((run.get("repository") or {}).get("full_name") or "")
    if actual_repo.lower() != repository.lower():
        problems.append(f"The run belongs to {actual_repo or 'an unknown repository'}, not {repository}.")
    head_repo = str((run.get("head_repository") or {}).get("full_name") or "")
    if head_repo and head_repo.lower() != repository.lower():
        problems.append(f"The run built code from a fork ({head_repo}).")
    path = str(run.get("path") or "").split("@")[0]
    if path not in BUILD_WORKFLOWS[mode]:
        problems.append(f"Run {run_id} is '{run.get('name') or path}' ({path or 'unknown workflow'}), which is "
                        f"not a {mode} build workflow. Expected one of: "
                        + ", ".join(sorted(item.rsplit('/', 1)[-1] for item in BUILD_WORKFLOWS[mode])) + ".")
    if run.get("status") != "completed":
        problems.append(f"The build run has not finished (status {run.get('status')!r}).")
    elif run.get("conclusion") != "success":
        problems.append(f"The build run did not succeed (conclusion {run.get('conclusion')!r}). A failed build "
                        "produces a package that cannot be approved.")
    if run.get("event") not in ALLOWED_EVENTS:
        problems.append(f"The build run was triggered by {run.get('event')!r}, which is not an accepted trigger.")
    if ref and str(run.get("head_branch") or "") != ref:
        problems.append(f"The build ran on branch {run.get('head_branch')!r} but this release runs on {ref!r}. "
                        "Build and release must use the same branch.")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(BUILD_WORKFLOWS))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args(argv)
    if not str(args.run_id).isdigit():
        print("::error::source_run_id must be the numeric id of a build run (never 'latest').")
        return 2
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or not args.repository:
        print("::error::GITHUB_TOKEN and GITHUB_REPOSITORY are required to verify the source run.")
        return 1
    try:
        run = fetch_run(args.repository, args.run_id, token, os.environ.get("GITHUB_API_URL") or "https://api.github.com")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"::error::Run {args.run_id} does not exist in {args.repository}.")
            return 2
        print(f"::error::GitHub API returned HTTP {exc.code} while reading run {args.run_id}.")
        return 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"::error::Could not read run {args.run_id} from the GitHub API: {type(exc).__name__}")
        return 1
    problems = problems_with(run, repository=args.repository, mode=args.mode, ref=args.ref, run_id=args.run_id)
    summary = {"run_id": str(args.run_id), "workflow": run.get("name"), "path": run.get("path"),
               "conclusion": run.get("conclusion"), "head_branch": run.get("head_branch"),
               "head_sha": run.get("head_sha"), "event": run.get("event"), "accepted": not problems,
               "problems": problems}
    print(json.dumps(summary, indent=2))
    for item in problems:
        print(f"::error::{item}")
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
