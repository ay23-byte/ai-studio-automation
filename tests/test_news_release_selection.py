import json
from unittest.mock import patch

from scripts.release_news_job import main


def test_release_uses_only_build_result_job(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"status": "built", "job_id": 42}))
    with patch("core.news_private_release.publish_news_job", return_value={"status": "uploaded"}) as publish:
        assert main(["--build-result", str(result)]) == 0
        publish.assert_called_once_with(42, wait=True)


def test_skipped_or_invalid_build_never_uploads(tmp_path):
    result = tmp_path / "result.json"
    with patch("core.news_private_release.publish_news_job") as publish:
        result.write_text(json.dumps({"status": "no_story"}))
        assert main(["--build-result", str(result)]) == 0
        result.write_text(json.dumps({"status": "built"}))
        assert main(["--build-result", str(result)]) == 3
        result.unlink()
        assert main(["--build-result", str(result)]) == 3
        publish.assert_not_called()
