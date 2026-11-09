import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.validate_archival_video_e2e import audit_archival_asset


class ArchivalVideoEndToEndTests(unittest.TestCase):
    def _fixture(self, root, metadata):
        video = root / "candidate.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=gray:s=640x360:d=3", "-an", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(video),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        video.with_suffix(".mp4.source.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        return video

    def _metadata(self):
        return {
            "provider": "Internet Archive",
            "asset_id": "fixture-1986",
            "title": "1986 historical newsreel",
            "page_url": "https://archive.org/details/fixture-1986",
            "creator": "Archive collection",
            "date_created": "1986",
            "licence": "Public Domain",
            "licence_url": "https://archive.org/details/fixture-1986",
            "relevance_score": 0.8,
            "asset_type": "archival_video_candidate",
            "archival_material": True,
            "archival_authenticity_status": "pending_human_verification",
        }

    def test_real_video_file_sidecar_and_review_manifest_pass_together(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg/ffprobe are required for the media integration test.")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = self._fixture(root, self._metadata())
            result = audit_archival_asset(video, "1986 historical newsreel", "1986", root / "review")
            self.assertEqual(result["status"], "passed")
            self.assertTrue(result["probe"]["duration_seconds"] >= 2)
            self.assertTrue(result["archival_provenance_complete"])
            self.assertEqual(result["archival_authenticity_status"], "pending_human_verification")
            self.assertTrue(result["human_review_required"])
            self.assertFalse(result["publication_ready"])
            self.assertFalse(result["youtube_upload_performed"])
            self.assertTrue(result["sample_frames"])

    def test_generic_stock_sidecar_is_rejected_as_archival(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg is required for the media integration test.")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = self._metadata()
            metadata.update({"provider": "Pixabay", "asset_type": "stock_video"})
            video = self._fixture(root, metadata)
            with self.assertRaisesRegex(ValueError, "Unexpected source provider|does not identify"):
                audit_archival_asset(video, "1986 historical newsreel", "1986", root / "review")

    def test_missing_rights_or_item_page_is_rejected(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg is required for the media integration test.")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = self._metadata()
            metadata.pop("licence_url")
            video = self._fixture(root, metadata)
            with self.assertRaisesRegex(ValueError, "Item page, licence"):
                audit_archival_asset(video, "1986 historical newsreel", "1986", root / "review")


if __name__ == "__main__":
    unittest.main()
