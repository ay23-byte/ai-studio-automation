# News Short advanced QA

The `scripts/qa_news_short.py` command combines the encoding profile, black-frame/silence warnings, audio loudness/peak/clipping analysis and sampled visual-quality checks. It writes separate audio and visual reports under `advanced_qa/`. A PASS is a technical screening result only; human editorial, rights, source, captions, audio and phone review remain required. Missing frame samples or failed audio/visual analysis blocks the gate. The workflow does not publish or upload anything.


## Shot-to-narration semantic QA

The renderer's `production_manifest.json` now records each scene's start time,
duration, narration, visual description, visual type, caption and source IDs.
When that manifest is beside the rendered MP4, `scripts/qa_news_short.py`
extracts one midpoint frame per scene and compares it with that scene's
narration and intended visual description using the existing Gemini Vision
screening function in `core/visual_semantics.py`.

The semantic report is written to `advanced_qa/semantic_qa_report.json`.
A clear mismatch blocks technical QA. Uncertain results, missing credentials,
model errors, or unavailable shot data require review and cannot be treated as
a semantic pass. If a legacy MP4 has no production manifest, the report says
`NOT_RUN`; semantic alignment has not been checked.

Run with an explicit manifest when it is not stored beside the MP4:

```powershell
python scripts/qa_news_short.py output/daily_news/rendered/review_short.mp4 --manifest output/daily_news/rendered/production_manifest.json
```

A semantic PASS means only that sampled frames appear to align with their
scene descriptions. It does not establish factual truth, historical
authenticity, identity, copyright, licence status, or source accuracy.
Human visual, editorial, fact, rights and phone-playback review remain
mandatory. No upload or publication is triggered by this QA stage.
