"""Milestone 3: research dossier, claim-to-source mapping and rejection."""
from __future__ import annotations

import datetime as dt
import unittest

from src.production_v2.models import Claim, Disagreement, ResearchDossier, Source
from src.production_v2.research import build_bibliography, reject_topic, validate_dossier


def dossier(*, demo: bool = False, established_source: str = "primary") -> ResearchDossier:
    sources = [
        Source("s1", "Official record", "Ministry", "https://example.org/record", "2026-01-01",
               established_source),
        Source("s2", "Independent report", "Reuters", "https://example.org/report", "2026-01-02",
               "reputable"),
    ]
    if demo:
        sources = [Source("d1", "Demo", "Demo", "https://example.invalid/a", "2026-01-01", "demo")]
    return ResearchDossier(
        topic="Test topic", content_type="news_short", thesis="A claim with a source is usable.",
        sources=sources,
        claims=[Claim("c1", "A recorded figure was published.", "established",
                      ["s1" if not demo else "d1"], "recorded excerpt", demo)],
        disagreements=[], demo_mode=demo)


class ResearchTests(unittest.TestCase):
    today = dt.date(2026, 11, 19)

    def test_valid_dossier_is_accepted(self):
        result = validate_dossier(dossier(), min_sources=2, today=self.today)
        self.assertTrue(result.accepted, result.issues)
        self.assertEqual(len(result.bibliography), 2)
        self.assertIn("Ministry", result.bibliography[0])

    def test_unsupported_claim_is_rejected(self):
        data = dossier()
        data.claims[0].source_ids = []
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertFalse(result.accepted)
        self.assertTrue(any("no source" in issue for issue in result.issues))

    def test_unknown_source_id_is_rejected(self):
        data = dossier()
        data.claims[0].source_ids = ["missing"]
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertFalse(result.accepted)

    def test_established_claim_needs_strong_source_outside_demo(self):
        data = dossier(established_source="unverified")
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertFalse(result.accepted)

    def test_bad_url_and_future_date_are_rejected(self):
        data = dossier()
        data.sources[0].url = "ftp://not-http"
        data.sources[1].published_at = "2099-01-01"
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertFalse(result.accepted)

    def test_demo_urls_are_only_allowed_in_demo_mode(self):
        demo_result = validate_dossier(dossier(demo=True), min_sources=1, today=self.today)
        self.assertTrue(demo_result.accepted, demo_result.issues)
        real = dossier(demo=True)
        real.demo_mode = False
        real.sources[0].url = "https://example.invalid/a"
        self.assertFalse(validate_dossier(real, min_sources=1, today=self.today).accepted)

    def test_allegation_warning_never_becomes_established(self):
        data = dossier()
        data.claims[0].status = "allegation"
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertTrue(result.accepted)
        self.assertTrue(any("allegation" in warning for warning in result.warnings))

    def test_disagreement_must_reference_a_known_claim(self):
        data = dossier()
        data.disagreements.append(Disagreement("unknown", "Sources disagree", ["s1"]))
        self.assertFalse(validate_dossier(data, min_sources=2, today=self.today).accepted)

    def test_topic_rejection_is_explicit(self):
        data = reject_topic(dossier(), "not enough independent evidence")
        result = validate_dossier(data, min_sources=2, today=self.today)
        self.assertFalse(result.accepted)
        self.assertIn("independent evidence", result.issues[0])

    def test_bibliography_labels_demo_sources(self):
        self.assertIn("NOT PUBLICATION EVIDENCE", build_bibliography(dossier(demo=True))[0])


if __name__ == "__main__":
    unittest.main()
