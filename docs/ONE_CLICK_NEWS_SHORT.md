# One-click news Short review workflow

Use **Actions → Create news Short review package — one click**.

1. Enter the approved news-story issue number (for example, `46`).
2. Click **Run workflow**.
3. Wait for the workflow to finish.
4. Download the single `news-short-review-RUN_ID` artifact.
5. Review the script, each scene visual, source sidecars, licence information and credits.

The workflow reads the evidence JSON packet directly from the issue, checks that it is marked approved and contains sources and verified facts, generates the script draft, prepares 4–6 visual candidates, and uploads one combined review artifact.

## What it does not do

- It does not render the final MP4.
- It does not upload to YouTube or publish.
- It does not approve the script or visual assets on the user's behalf.
- It does not bypass the source-review gate.

If the issue is not open, does not have the expected `News story review:` title, or its evidence packet is not approved, the workflow stops with an explanation.

The older multi-step workflows remain available for existing runs. For a new news Short, use this one-click workflow instead.
