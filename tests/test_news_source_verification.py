import unittest
from datetime import datetime, timezone
from core.news_source_verification import classify_source, verify_fact_sources

NOW = datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc)

class NewsSourceVerificationTests(unittest.TestCase):
    def source(self, sid, domain, wire="", published="2026-11-07T05:00:00Z"):
        return {"source_id": sid, "url": "https://" + domain + "/story",
                "published": published, "origin": wire}

    def test_domain_matching_rejects_lookalike(self):
        self.assertEqual(classify_source(self.source("x", "reuters.com.evil.test")), "unknown")
        self.assertEqual(classify_source(self.source("x", "sub.reuters.com")), "tier2")

    def test_primary_source_is_sufficient_for_its_own_statement(self):
        fact = {"source_ids": ["p"]}
        result = verify_fact_sources(fact, [self.source("p", "pib.gov.in")], now=NOW)
        self.assertTrue(result["ok"])

    def test_two_independent_tier_two_sources_pass(self):
        fact = {"source_ids": ["a", "b"]}
        result = verify_fact_sources(fact, [self.source("a", "reuters.com"),
            self.source("b", "thehindu.com")], now=NOW)
        self.assertTrue(result["ok"])

    def test_same_wire_counts_once(self):
        fact = {"source_ids": ["a", "b"]}
        result = verify_fact_sources(fact, [self.source("a", "reuters.com", "Reuters"),
            self.source("b", "thehindu.com", "Reuters")], now=NOW)
        self.assertFalse(result["ok"])

    def test_social_and_unknown_never_confirm(self):
        fact = {"source_ids": ["a", "b"]}
        result = verify_fact_sources(fact, [self.source("a", "reddit.com"),
            self.source("b", "unknown.example")], now=NOW)
        self.assertFalse(result["ok"])

    def test_stale_major_source_fails(self):
        fact = {"source_ids": ["a", "b"]}
        result = verify_fact_sources(fact, [self.source("a", "reuters.com", published="2026-11-01T05:00:00Z"),
            self.source("b", "thehindu.com")], now=NOW)
        self.assertFalse(result["ok"])

if __name__ == "__main__":
    unittest.main()
