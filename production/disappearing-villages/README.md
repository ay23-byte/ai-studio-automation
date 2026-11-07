# The Mystery of India's Disappearing Villages

Prepared documentary package for the 12-minute Hinglish investigative film.

## Editorial assets

- `facts.json` — research dossier with source URLs, confidence labels, historical dates, conflicts, counter-arguments and open research questions.
- `outline.json` — story architecture, chapter timing, hooks, bridges, reveals and callbacks.
- `script.json` — 1,837-word narration (about 12.25 minutes at 150 wpm), source/claim registry and 34 narration-anchored visual beats across nine sections.
- `packaging.json` — title options, thumbnail prompts, description, tags, chapter markers, pinned comment and Shorts cut suggestions.
- `qa_preflight.json` — factual, legal, visual and release checks that remain open for human review.

## Render the prepared script

The new GitHub Actions workflow `Render prepared documentary package` is manually triggered after this branch is merged. It uses:

```text
python scripts/render_editorial_package.py --script production/disappearing-villages/script.json --voice-gender female --language hinglish
```

The render entry point deliberately bypasses a second AI script-generation call. It uses the saved editorial script as the source of truth, validates that every section has at least three exact, chronological narration anchors, then invokes the existing production pipeline for TTS, scene-specific visuals, editing, licensing/provenance, visual-review evidence and technical QA.

Expected build evidence includes (subject to successful provider calls and pipeline completion):

- rendered MP4 under `output/`
- `release_metadata.json`
- research verification report
- `edit_decision_list.json`
- `licenses.csv` and credits text
- `visual_review_report.json` and pending reviewer decisions
- video/audio quality reports
- thumbnails and caption assets

## Important limits

1. The 43,324 and 1,048 counts refer to Census 2011. The 734 and district/service figures refer to a 2018 survey reproduced in a 2021 research report. None is presented as a current 2026 count.
2. The Uttarakhand Migration Commission figures are reproduced in the cited research report. Inspect the original report, denominators and question wording before publication.
3. PLFS migration rates are not counts of permanently abandoned villages.
4. The 660/482/399/358 service-gap categories overlap and must not be summed.
5. Stock search and metadata are not visual approval. Check each frame for actual relevance, watermarks, logos, identifiable people and misleading context.
6. AI reconstructions are illustrative, not archival footage. Disclose realistic synthetic visuals as required by YouTube.
7. The female `still_cpu` presenter is a subtly animated portrait, not lip-sync. Do not claim otherwise.
8. The packaging also cites the Ministry of Home Affairs' 25 March 2026 update: BADP was described as in its sunset phase, while VVP-I and VVP-II approvals were reported. Approval counts are not treated as proof of local outcomes.

No YouTube upload or public publishing is performed by this workflow. A human must watch the complete render, inspect licences and source claims, approve every visual-review item, and make the publication decision.

## Runtime note

The script contains 1,837 whitespace-delimited narration tokens, an estimate of 12.25 minutes at 150 words per minute. Real TTS duration is authoritative. Regenerate chapter timestamps from the final audio before publishing.
