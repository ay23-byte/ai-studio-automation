# GitHub news story approval workflow

This workflow uses GitHub Issues as the editorial review surface. It does not treat an RSS candidate or a chat approval as source verification.

## 1. Create a review issue

Open Actions → News story approval — create review request → Run workflow. Enter the candidate ID, exact headline, original source URL, publisher, and publication timestamp in ISO-8601 format. The workflow creates a review issue with a JSON packet and a human-review checklist.

For the current Himalayan construction candidate:
- Candidate ID: ecdebdd7338d38cc0540
- Headline: Building boom puts 300,000 people in the path of potential Himalayan disasters
- Original source: https://www.bbc.co.uk/news/articles/c574pj3zljvjo?at_medium=RSS&at_campaign=rss
- Original publication time: 2026-10-28T23:11:59Z

## 2. Complete the source review in the issue

Open each original source. Add independent, eligible sources and record facts in your own words. Every fact must contain fact, source_ids, and a specific human-written evidence_note. Source records need source_id, url, and an ISO-8601 published timestamp. Add title and syndication/wire metadata where known.

For breaking stories, the deterministic gate requires sources published within 48 hours. It accepts a tier-1 primary source for its own statement, or two independent eligible sources including a tier-2 outlet. A stale original discovery link can remain in the packet as context but cannot confirm a breaking-news fact. The source gate checks metadata and source tiers; it does not browse pages or prove that a source supports a fact. Human passage inspection remains mandatory.

Only after completing the checklist, change review_status from pending_human_verification to approved in the single JSON block and save the issue.

## 3. Request a script draft

Add a new issue comment containing exactly /approve-script. The comment workflow accepts commands only from repository owners, members, or collaborators. It extracts the JSON packet and calls the existing reviewed-script writer, which independently reruns source verification before any Gemini request.

On success, the workflow uploads both JSON files as a 14-day Actions artifact named `news-script-draft-RUN_ID` (where RUN_ID is the GitHub Actions run ID) and posts its URL on the issue. Copy the numeric run ID from that workflow run page; the next asset-preparation workflow uses it to download the exact artifact. On failure, it posts a pointer to the workflow logs.

## Approval boundaries

- The initial workflow dispatch creates a review request; it does not approve facts.
- /approve-script authorizes script drafting only after the reviewed packet passes.
- The generated script remains a draft requiring human factual, editorial and visual review.
- Production approval is separate and remains required before narration or rendering.
- This workflow does not render MP4, upload to YouTube, schedule, or publish.
