import unittest
from datetime import datetime, timezone
from core.news_source_verification import classify_source, verify_fact_sources

NOW = datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc)
TRUSTED = {
    "tier1_primary": ["pib.gov.in"],
    "tier2_major": ["reuters.com", "thehindu.com"],
    "tier3_specialist": ["nature.com"],
    "social_lead_only": ["reddit.com"],
    "peer_reviewed_primary_research_domains": ["nature.com"],
    "wire_markers": ["Reuters"],
    "freshness_hours": {"breaking": 48, "explainer": 168},
}


class NewsSourceVerificationTests(unittest.TestCase):
    def source(self, sid, domain, wire="", published="2026-11-07T05:00:00Z", **extra):
        return {"source_id": sid, "url": "https://" + domain + "/story",
                "published": published, "origin": wire, **extra}

    def test_domain_matching_rejects_lookalike(self):
        self.assertEqual(classify_source(self.source("x", "reuters.com.evil.test")), "unknown")
        self.assertEqual(classify_source(self.source("x", "sub.reuters.com")), "tier2")

    def test_primary_source_is_sufficient_for_its_own_statement(self):
        result = verify_fact_sources({"source_ids": ["p"]}, [self.source("p", "pib.gov.in")],
                                     now=NOW, trusted=TRUSTED)
        self.assertTrue(result["ok"])

    def test_two_independent_tier_two_sources_pass(self):
        result = verify_fact_sources({"source_ids": ["a", "b"]},
            [self.source("a", "reuters.com"), self.source("b", "thehindu.com")],
            now=NOW, trusted=TRUSTED)
        self.assertTrue(result["ok"])

    def test_same_wire_counts_once(self):
        result = verify_fact_sources({"source_ids": ["a", "b"]},
            [self.source("a", "reuters.com", "Reuters"),
             self.source("b", "thehindu.com", "Reuters")], now=NOW, trusted=TRUSTED)
        self.assertFalse(result["ok"])

    def test_social_and_unknown_never_confirm(self):
        result = verify_fact_sources({"source_ids": ["a", "b"]},
            [self.source("a", "reddit.com"), self.source("b", "unknown.example")],
            now=NOW, trusted=TRUSTED)
        self.assertFalse(result["ok"])

    def test_stale_major_source_fails(self):
        result = verify_fact_sources({"source_ids": ["a", "b"]},
            [self.source("a", "reuters.com", published="2026-11-01T05:00:00Z"),
             self.source("b", "thehindu.com")], now=NOW, trusted=TRUSTED)
        self.assertFalse(result["ok"])

    def test_peer_reviewed_primary_research_passes_outside_news_freshness_window(self):
        source = self.source("paper", "nature.com", published="2026-10-08T00:00:00Z",
                             source_type="peer_reviewed_primary_research", peer_reviewed=True)
        result = verify_fact_sources({"source_ids": ["paper"]}, [source],
                                     breaking=False, now=NOW, trusted=TRUSTED)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sources"][0]["evidence_basis"],
                         "reviewed peer-reviewed primary research")

    def test_nature_article_without_research_evidence_metadata_does_not_get_exception(self):
        source = self.source("article", "nature.com", published="2026-10-08T00:00:00Z")
        result = verify_fact_sources({"source_ids": ["article"]}, [source],
                                     breaking=False, now=NOW, trusted=TRUSTED)
        self.assertFalse(result["ok"])

    def test_unlisted_domain_cannot_claim_peer_reviewed_research_exception(self):
        source = self.source("paper", "unlisted-journal.example",
                             source_type="peer_reviewed_primary_research", peer_reviewed=True)
        result = verify_fact_sources({"source_ids": ["paper"]}, [source],
                                     breaking=False, now=NOW, trusted=TRUSTED)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
