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
3. **Claim ledger:** split the script into checkable claims. Mark each as supported, disputed, context-needed or unverified. Never turn an AI-generated suggestion into a fact.
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

## AI presenter reality check

The current repository can generate scripts, narration, scene-matched visual prompts, stock-footage searches, subtitles, thumbnails and FFmpeg-edited videos. It does **not** currently create a photorealistic talking-head avatar or lip-sync a face to narration. Do not label a still image, looping clip or ordinary TTS as an AI presenter.

A genuine presenter feature needs a separate, tested talking-head/lip-sync model or a user-supplied licensed presenter video. Many hosted avatar services are paid; free local models may require a capable GPU and have setup/licensing constraints. Until a provider is selected and validated, use documentary voiceover plus relevant visuals and keep presenter shots as an optional manually supplied asset.

## Minimum release gate

- [ ] Research dossier and claim ledger reviewed
- [ ] Every high-impact claim has a source
- [ ] Presenter/voice is not impersonating a real person
- [ ] Visuals match narration and have usage rights
- [ ] Captions and Hindi pronunciation checked
- [ ] Audio and full video reviewed end-to-end
- [ ] Thumbnail/title accurately represent the episode
- [ ] Human approval recorded before upload
