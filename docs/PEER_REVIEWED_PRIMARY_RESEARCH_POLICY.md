# Peer-reviewed primary research evidence policy

A peer-reviewed original research article is primary evidence for the results reported in that article. Its publication date does not make those study results expire after the news freshness window. The freshness window continues to apply to secondary reporting and current-event claims.

## Eligibility

The verifier grants this scoped exception only when:
1. Source metadata declares `source_type: peer_reviewed_primary_research`.
2. Source metadata declares `peer_reviewed: true`.
3. The host is explicitly listed in `peer_reviewed_primary_research_domains` in `config/trusted_sources.json`.
4. The source is not blocked by the repository blocklist.
5. A human editor has inspected the original paper and confirmed that the cited passage, table, figure, or supplement supports the exact fact.

This does not promote every page on the journal domain to tier 1. It does not exempt news coverage from freshness rules. The verifier checks metadata and provenance only; it cannot establish factual support or peer-review status on its own.

## Application to the Himalayan study

The *Scientific Reports* article “Increasing exposure to potential flooding in high-mountain catchments” (published 8 October 2026; DOI 10.1038/s41598-026-70268-0) is the original peer-reviewed study. It reports the 293,800 / 18% population estimate, the 46% change in building footprint area within 0–50 m HAND, the 77% change in building area within modelled GLOF extents, and Nepal subset estimates. The paper also discusses uncertainty in building footprints and population projections.

The Kathmandu Post and WorldPop articles are recent contextual corroboration, not independent scientific replications. BBC remains background because it is outside the seven-day explainer-news window.

## Editorial safeguards

- Describe 293,800 as estimated potential exposure under the HAND proxy, not a prediction that all will be flooded.
- Describe the 77% metric as building footprint area within modelled GLOF extents, not a building count.
- Preserve uncertainty bounds and limitations.
- Keep unrelated, current, or event-specific claims subject to normal freshness and independent-source rules.
- Never change publication dates, claim independent replication, or automatically mark a packet human-approved.
