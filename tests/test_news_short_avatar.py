import json
import tempfile
import unittest
from pathlib import Path

from core.news_short_avatar import add_3d_presenter


class NewsShortAvatarTests(unittest.TestCase):
    def test_requires_completed_review_render(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileNotFoundError, "completed review render"):
                add_3d_presenter(temp)

    def test_rejects_manifest_outside_reviewed_scene_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "review_short.mp4").write_bytes(b"fake")
            (root / "production_manifest.json").write_text(json.dumps({
                "shots": [{"start_seconds": 0, "duration_seconds": 2}] * 3
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "4–6 scene"):
                add_3d_presenter(root)

    def test_requires_exact_scene_narration_audio_before_avatar_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "review_short.mp4").write_bytes(b"fake")
            (root / "production_manifest.json").write_text(json.dumps({
                "shots": [
                    {"start_seconds": i * 2, "duration_seconds": 2}
                    for i in range(4)
                ]
            }), encoding="utf-8")
            with self.assertRaisesRegex(FileNotFoundError, "Scene 1 narration audio"):
                add_3d_presenter(root)


if __name__ == "__main__":
    unittest.main()
