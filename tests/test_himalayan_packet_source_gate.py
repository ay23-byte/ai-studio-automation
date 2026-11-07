import unittest
from datetime import datetime, timezone

from core.news_source_verification import verify_packet


class HimalayanPacketSourceGateTests(unittest.TestCase):
    def test_himalayan_study_facts_pass_using_original_peer_reviewed_paper(self):
        packet = {
            "story_type": "explainer",
            "sources": [
                {
                    "source_id": "S2",
                    "title": "Increasing exposure to potential flooding in high-mountain catchments",
                    "url": "https://www.nature.com/articles/s41598-026-70268-0",
                    "published": "2026-10-08T00:00:00Z",
                    "origin": "Scientific Reports / Springer Nature",
                    "source_type": "peer_reviewed_primary_research",
                    "peer_reviewed": True,
                },
                {
                    "source_id": "S3",
                    "title": "Construction boom pushes Nepal’s mountain settlements into flood-prone areas",
                    "url": "https://kathmandupost.com/climate-environment/2026/11/02/construction-boom-pushes-nepal-s-mountain-settlements-into-flood-prone-areas",
                    "published": "2026-11-02T15:23:00+05:45",
                    "origin": "The Kathmandu Post",
                },
            ],
            "verified_facts": [
                {"fact": "The study estimates 293,800 people within 0–50 m HAND in 2026.",
                 "source_ids": ["S2"], "evidence_note": "Original paper abstract and results."},
                {"fact": "Building area within 0–50 m HAND increased by about 46% from 2016 to 2023.",
                 "source_ids": ["S2"], "evidence_note": "Original paper reports 12.38 to 18.04 km²."},
                {"fact": "Building area within modelled GLOF extents increased by about 77% from 2016 to 2023.",
                 "source_ids": ["S2"], "evidence_note": "Original paper reports 0.72 to 1.28 km² and uncertainty."},
                {"fact": "For Nepal glacial-lake flow paths, 108,500 people were within 0–50 m HAND and 22,800 within 0–10 m HAND.",
                 "source_ids": ["S2"], "evidence_note": "Original paper supplementary results."},
            ],
        }
        result = verify_packet(
            packet,
            breaking=False,
            now=datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(result["safe_to_draft"], result)
        self.assertEqual(result["failed_fact_count"], 0)
        self.assertEqual(len(result["fact_checks"]), 4)
        for fact_check in result["fact_checks"]:
            self.assertEqual(fact_check["sources"][0]["evidence_basis"],
                             "reviewed peer-reviewed primary research")


if __name__ == "__main__":
    unittest.main()
