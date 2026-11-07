import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.editorial_review import load_reviews, save_review
from core.release_certification import assert_release_certified, certify_documentary
from core.research_verification import (
    save_claim_review, save_coverage_review, save_source_review,
    write_research_verification_report,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "FFmpeg/FFprobe are required for the release-certification smoke test")
class ReleaseCertificationSmokeTests(unittest.TestCase):
    def test_real_mp4_passes_end_to_end_release_certification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "documentary.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "5", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                "-movflags", "+faststart", str(video),
            ], check=True, timeout=180)

            visual = {
                "shot_count": 1, "semantic_screening_enabled": False,
                "shots": [{
                    "shot_id": "shot_0001", "chapter_id": "chapter_1",
                    "media_path": str(video), "media_type": "video",
                    "narration": "A sample ship enters the harbour.",
                    "visual_prompt": "Wide view of a ship entering a harbour.",
                    "visual_quality": {"status": "pass"},
                }],
            }
            (root / "visual_review_report.json").write_text(
                json.dumps(visual, indent=2), encoding="utf-8"
            )
            load_reviews(root / "visual_review_report.json", visual["shots"])
            save_review(
                root / "visual_review_report.json", "shot_0001",
                status="approved", reviewer_notes="Sample visual matches narration.",
                source_review_status="reviewed",
                rights_review_status="reviewed",
                rights_notes="Synthetic test visual; no third-party asset used.",
                licence_name="Synthetic test asset",
                attribution_text="Not applicable.",
            )
            (root / "visual_consistency_report.json").write_text(json.dumps({
                "status": "PASS",
                "summary": {"fail_count": 0, "review_count": 0, "unavailable_count": 0},
                "shots": [],
            }), encoding="utf-8")
            (root / "audio_quality_report.json").write_text(json.dumps({
                "status": "PASS", "human_review_status": "reviewed",
                "metrics": {"integrated_lufs": -16.0, "true_peak_dbfs": -1.5},
            }), encoding="utf-8")
            (root / "quality_report.json").write_text(json.dumps({
                "status": "PASS", "score": 100,
            }), encoding="utf-8")

            script = {
                "sources": [{
                    "source_id": "S1", "title": "Synthetic test source",
                    "publisher": "Test harness", "published_at": "2026-01-01",
                    "url": "https://example.org/test-source",
                }],
                "claims": [{
                    "claim_id": "C1", "text": "This is a synthetic test claim.",
                    "claim_type": "fact", "source_ids": ["S1"],
                    "evidence": "Synthetic fixture evidence.",
                }],
            }
            research = write_research_verification_report(script, [], root)
            (root / "release_metadata.json").write_text(json.dumps({
                "title": "Synthetic release test",
                "description": "Sources & further reading: https://example.org/test-source",
                "tags": ["test"],
            }), encoding="utf-8")
            save_source_review(root, research, "S1", "reviewed",
                               "Opened synthetic test source fixture.")
            save_claim_review(root, research, "C1", "verified",
                              "Checked synthetic fixture claim and source linkage.")
            save_coverage_review(root, research, True,
                                 "Reviewed the complete synthetic narration fixture.")

            certificate = certify_documentary(root, video)
            self.assertEqual(certificate["status"], "CERTIFIED", certificate["blockers"])
            self.assertEqual(assert_release_certified(root, video)["status"], "CERTIFIED")


if __name__ == "__main__":
    unittest.main()
