# Engineering Physics — Beyond Science documentary pilot

## Purpose

Test a Curistaan-style *editorial format* on the existing Engineering Physics channel without copying Curistaan's scripts, thumbnails, branding, footage or distinctive creative assets.

The pilot uses original Hinglish documentaries and Shorts about how technology, science, engineering, economics, history and geopolitics shape the modern world.

## Channel positioning

**Series name:** Beyond Science — World Explained  
**Channel:** Engineering Physics  
**Narration:** natural Hinglish in Roman script  
**Subtitles:** English  
**Audience promise:** complex systems and world events explained through evidence, mechanisms, history and human consequences.

### Audience transition

The current channel has a physics/engineering identity. Do not abruptly flood it with unrelated political commentary. Begin with bridge topics—semiconductors, Taiwan's chip supply chain, GPS, nuclear energy, the Space Race, energy routes and megaprojects. Broader country/economic stories follow after the first pilot results.

## Editorial pillars

| Pillar | Pilot share | Subject examples |
|---|---:|---|
| Technology and power | 30% | Semiconductors, AI infrastructure, satellites, energy |
| Countries and economics | 25% | Industrialization, trade, infrastructure, economic transformation |
| Geopolitics and history | 25% | International relations, conflict history, strategic resources |
| Science and infrastructure | 20% | Megaprojects, transport, communications, energy systems |

## Publishing cadence

- One 18–25 minute documentary each week; default 22 minutes.
- Three 35–58 second Shorts each week during the pilot.
- At least two Shorts should be derived from the week's documentary; one can be a related standalone explainer.
- Do not turn the existing daily physics Shorts discovery schedule into automatic geopolitics publishing during the pilot.
- Use the manual review workflows and the existing duplicate ledger.

## Long-form narrative blueprint

1. **0:00–0:30 — Cold open:** one documented contradiction, surprising fact or consequence. No greeting.
2. **0:30–2:00 — Promise and stakes:** the central question, why it matters and what the video will establish.
3. **2:00–6:00 — Origins:** geography, history, actors and context.
4. **6:00–12:00 — Turning points:** documented decisions, events and named case studies.
5. **12:00–17:00 — Mechanism:** explain how the technology, economy, institution or geopolitical system actually works.
6. **17:00–21:00 — Consequences and counterarguments:** people affected, trade-offs, alternative explanations and uncertainty.
7. **21:00–22:00+ — Resolution:** answer the opening question, identify what remains uncertain and give a proportionate CTA.

Adjust chapter lengths to the subject; this is a planning guide, not a rigid script template. Do not pad narration to hit a runtime.

## Shorts blueprint

- 0–2 seconds: visual and verbal hook.
- 2–10 seconds: context.
- 10–40 seconds: one mechanism, turning point or surprising documented detail.
- 40–52 seconds: consequence or takeaway.
- Final seconds: concise CTA to the related documentary.
- Keep each Short understandable on its own. No misleading cliffhangers or unsupported claims.

## Packaging rules

- Titles should express a specific question, transformation or contradiction.
- Thumbnails: one dominant subject, one visual metaphor, high contrast, minimal original text (2–5 words), accurate representation of the video.
- Avoid imitating Curistaan's thumbnail layouts, logo, fonts, wording or recurring visual identity.
- Description should state the subject, scope, research date and key sources.
- Use chapters and source references in the description where available.

## Research and rights

- Prefer primary sources, official datasets, academic research, institutional publications and reputable reporting.
- Every material factual claim needs source records and a human claim-level review.
- Separate facts, estimates, allegations, analysis and predictions.
- No fabricated interviews, quotes, statistics, documents or firsthand experiences.
- AI visuals are labelled as illustrative; never pass them off as archival footage.
- Every external visual requires its original source page, actual licence, licence URL and attribution.
- Preserve the existing technical QA, asset QA, editorial review, rights review and private-release gates.

## Automation usage

- Long-form: GitHub Actions → **2. Create and upload a Documentary — automatic**. This workflow builds a review package only; despite its legacy name, it does not upload.
- Shorts: GitHub Actions → **1. Build a Short — review required**. Provide a topic and choose one of the new categories. It also builds a review package only.
- Start with a manually selected topic from `config/documentary_topic_bank.json`.
- Set language to Hinglish.
- Long-form default target is 22 minutes; available targets are 18, 20, 22 and 25 minutes.
- Inspect the artifact and approve it manually. Only then use the separate private-release workflow.

## Eight-week pilot and measurement

Review at week 4 and week 8. Compare against the channel's own recent baseline, not another creator's private analytics.

Track:
- Impressions click-through rate.
- First 30-second retention.
- Average view duration and percentage viewed.
- Returning viewers.
- Subscribers gained per video.
- Shorts viewed vs swiped away.
- Long-form viewers who continue to another episode.

Do not change the whole channel identity based on one upload. Keep the physics/engineering promise visible until the data supports a broader repositioning.
