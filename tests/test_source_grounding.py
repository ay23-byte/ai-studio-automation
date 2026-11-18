"""Regression tests for original-article evidence and automated release."""
import json
from pathlib import Path

from core import source_grounding
from core.jobs import stable_hash
from core.release_gate import evaluate


def test_article_selection_skips_homepage_and_aggregator():
    source = {"url": "https://www.reuters.com/", "link": "https://news.google.com/articles/abc",
              "article_url": "https://www.reuters.com/world/example-story-2026-01-01/"}
    assert source_grounding.article_url(source) == source["article_url"]
    assert source_grounding.article_url({"url": source["url"], "link": source["link"]}) == ""


def test_automated_news_gate_requires_script_bound_grounding(tmp_path):
    script = {"title": "News", "scenes": [{"say": "A test event occurred."}]}
    report = {"status": "PASS", "issues": [], "script_hash": stable_hash(source_grounding.units(script)),
              "sources": [{"source_id": "N1"}], "scenes": [{"scene": 1, "verdict": "supported",
              "evidence": [{"source_id": "N1", "quote": "A test event occurred."}]}]}
    (tmp_path / "verification.json").write_text(json.dumps({"status": "PASS"}))
    (tmp_path / "source_grounding.json").write_text(json.dumps(report))
    row = {"id": 1, "format": "news_short", "status": "done", "script_json": json.dumps(script)}
    def grounding_check():
        return next(c for c in evaluate(row, tmp_path, require_human_review=False)["checks"]
                    if c["check"] == "news_source_grounding")
    assert grounding_check()["status"] == "PASS"
    script["scenes"][0]["say"] = "A different claim."
    row["script_json"] = json.dumps(script)
    assert grounding_check()["status"] == "FAIL"
    (tmp_path / "source_grounding.json").unlink()
    assert grounding_check()["status"] == "FAIL"


def test_segment_cache_fingerprint_uses_bytes_not_mtime(tmp_path):
    import os
    from core.editor_longform import _segment_key
    source = tmp_path / "clip"
    source.write_bytes(b"first")
    original = source.stat()
    first = _segment_key(source, 5, False, {})
    source.write_bytes(b"other")
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert _segment_key(source, 5, False, {}) != first
