# News Short advanced QA

The `scripts/qa_news_short.py` command combines the encoding profile, black-frame/silence warnings, audio loudness/peak/clipping analysis and sampled visual-quality checks. It writes separate audio and visual reports under `advanced_qa/`. A PASS is a technical screening result only; human editorial, rights, source, captions, audio and phone review remain required. Missing frame samples or failed audio/visual analysis blocks the gate. The workflow does not publish or upload anything.
