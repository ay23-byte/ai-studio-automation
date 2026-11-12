import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render_standard_short_review import main


class StandardShortReviewEntryTests(unittest.TestCase):
    def test_rejects_missing_scenes_before_creating_job(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "script.json"
            script.write_text(json.dumps({"title": "No scenes", "scenes": []}))
            with patch("sys.argv", ["render_standard_short_review.py", "--script", str(script)]):
                with self.assertRaisesRegex(ValueError, "non-empty scenes"):
                    main()

    def test_rejects_scene_without_narration(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "script.json"
            script.write_text(json.dumps({"title": "Bad scene", "scenes": [{"caption": "No narration"}]}))
            with patch("sys.argv", ["render_standard_short_review.py", "--script", str(script)]):
                with self.assertRaisesRegex(ValueError, "non-empty 'say'"):
                    main()


if __name__ == "__main__":
    unittest.main()
