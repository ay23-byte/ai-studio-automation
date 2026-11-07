# Documentary Channel Production Standard

## Channel direction

- Format: Hindi/Hinglish educational documentaries and explainers.
- Primary runtime: 10–15 minutes; default target 12 minutes.
- Subjects: geopolitics, history, science, technology, economics, India and carefully researched mysteries.
- Presentation: a recurring AI-human presenter for selected on-camera moments, supported by documentary voiceover, cinematic B-roll, maps, diagrams, timelines and data visualizations.
- Editorial identity: curious, evidence-led, accessible, calm and visually consistent. Do not imitate another creator's wording, branding, thumbnails or exact editing.

## Episode workflow

1. **Topic brief:** define the viewer question, why it matters, geography/time period and what the video will not claim.
2. **Research dossier:** collect primary documents and reputable reporting. Record title, publisher, publication date, URL, relevant passage/data and the exact claim it supports. Prefer primary sources for official figures, laws, speeches and datasets.
3. **Claim ledger:** split the script into checkable claims. Link each material factual/numeric assertion to source IDs and evidence notes. Mark each as pending, verified, disputed or unsupported only after a human opens the source. Never turn an AI-generated suggestion into a fact.
4. **Narrative:** cold open, promise, context, chronological/logical chapters, examples, counterarguments, uncertainty and a resolved conclusion. Aim for approximately 125–145 spoken words per minute, then measure the actual narration duration.
5. **Storyboard:** every shot must support the sentence being spoken. Specify shot purpose, exact narration anchor, visual description, asset type, source/credit and expected duration.
6. **Presenter plan:** use the presenter for greeting-free hooks, chapter bridges and direct explanations. Use documentary voiceover over evidence, maps, charts and B-roll. Do not use a synthetic presenter to impersonate a real person.
7. **Audio:** one consistent narrator, natural pauses, pronunciation review, voice/music separation and restrained sound design.
8. **Edit:** 16:9, 1080p minimum, readable captions, deliberate pacing, restrained transitions, no unrelated filler footage.
9. **Review:** watch the entire export; verify facts, source credits, pronunciation, caption timing, visual relevance, audio levels, black frames and ending.
10. **Approval:** keep the output private/unlisted until a human approves it. Never enable automatic public upload by default.

## Research and fact-check rules

- Distinguish confirmed facts, estimates, allegations, analysis and predictions.
- For time-sensitive subjects, display the date of the information and re-check it immediately before publication.
- Political and conflict coverage must include credible perspectives and context; avoid presenting one side's claims as independently verified.
- Statistics require source, date, unit, geography and methodology where available.
- If sources disagree, explain the disagreement instead of silently choosing one.
- Never invent URLs, citations, quotes, experts, footage provenance or market data.
- A claim audit based only on user-provided text is not external fact-checking.

## Visual rules

- Use licensed/permissioned footage and retain asset/source records.
- Use maps with clear labels, dates and boundaries; avoid misleading map crops.
- Build charts from cited data, not generated numbers.
- AI-generated images are illustrative, not documentary evidence. Do not present generated scenes as authentic footage.
- Avoid repetitive stock clips, unrelated visuals and fake archival footage.
- Use a consistent dark-neutral documentary palette with one accent color, restrained lower thirds and a readable Devanagari-compatible font.

## AI presenter integration

The repository includes an **opt-in SadTalker adapter** in `core/presenter.py`. When enabled, it extracts the opening narration segment for each chapter, passes that audio and an owned/licensed portrait to an existing SadTalker installation, and inserts the generated talking-head clip before that chapter's B-roll.

Configuration:

```dotenv
PRESENTER_ENABLED=1
SADTALKER_DIR=/path/to/SadTalker
PRESENTER_IMAGE=/path/to/your-licensed-presenter-portrait.png
```

**Deployment limitation:** ordinary GitHub-hosted Actions runners do not provide a supported NVIDIA GPU. The long-form workflow can provision SadTalker in a separate Python 3.8 CPU environment, but CPU inference is very slow; use `SADTALKER_DEVICE=cuda` only on a compatible GPU workstation/self-hosted runner. The adapter is opt-in and is not an instant cloud-avatar service. It validates media streams and A/V duration, but does not certify phoneme-perfect lip-sync. Check the current upstream code, model-weight and portrait licensing before commercial use. If presenter generation is unavailable, the system clearly labels the still-portrait fallback as not lip-synced.

## Minimum release gate

- [ ] Research dossier and claim ledger reviewed
- [ ] Every material factual/numeric claim appears in the claim ledger and is verified against the original source
- [ ] Complete narration checked for uncited or omitted factual claims
- [ ] Presenter/voice is not impersonating a real person
- [ ] Visuals match narration and have a documented asset-specific rights/licence and attribution review
- [ ] Captions and Hindi pronunciation checked
- [ ] Audio and full video reviewed end-to-end
- [ ] Thumbnail/title accurately represent the episode
- [ ] Human approval recorded before upload


### Optional SadTalker configuration

```dotenv
PRESENTER_ENABLED=0
SADTALKER_DIR=
PRESENTER_IMAGE=
```

Only enable after installing SadTalker and its required checkpoints on a GPU-capable machine/runner. Use a portrait you own or have permission to animate. The adapter creates short chapter-opening presenter shots; the rest of each chapter remains matched documentary B-roll.


## Research and rights review implementation

The production app writes `research_verification_report.json` and `research_review_decisions.json` for each long-form build. Use the **Research, citations & rights verification** Streamlit page to open original sources, record claim-level verification notes and attest that the complete narration is represented. Source URLs and GDELT discovery matches are not proof of claim support.

Use the **Visual Editorial Review** page to record a separate per-shot rights decision, licence basis, attribution and notes. Provenance review and rights clearance are different checks. The release gate requires both; changed source metadata invalidates previous shot and rights approval.
