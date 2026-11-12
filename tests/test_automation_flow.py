"""Topic queue, scheduler, notifications, autonomous news, state persistence and workflow hygiene."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import config, dedupe, news_auto, notify, topics

ROOT = Path(__file__).resolve().parents[1]
QUIET_ENV = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "DISCORD_WEBHOOK_URL": "",
             "SLACK_WEBHOOK_URL": "", "NOTIFY_WEBHOOK_URL": "", "GITHUB_STEP_SUMMARY": ""}


class _StateSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for item in (patch.object(config, "STATE_DIR", self.root / "state"),
                     patch.object(config, "LOG_DIR", self.root / "logs"),
                     patch.object(config, "BUILD_DIR", self.root / "build"),
                     patch.object(config, "OUTPUT_DIR", self.root / "output"),
                     patch("core.dedupe.known_titles_from_db", return_value=[]),
                     patch.dict(os.environ, QUIET_ENV)):
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)
        self.queue = self.root / "topics.json"

    def write_queue(self, items):
        self.queue.write_text(json.dumps(items), encoding="utf-8")


class TopicQueueTests(_StateSandbox):
    def test_done_unapproved_and_wrong_format_entries_are_skipped(self):
        self.write_queue([{"title": "A done", "done": True},
                          {"title": "B not approved", "approved": False},
                          {"title": "C documentary", "format": "long"},
                          {"title": "How radar measures distance", "format": "shorts"}])
        _, topic = topics.next_topic("shorts", self.queue)
        self.assertEqual(topic["title"], "How radar measures distance")
        _, long_topic = topics.next_topic("long", self.queue)
        self.assertEqual(long_topic["title"], "C documentary")

    def test_already_produced_subject_is_skipped_and_marked(self):
        dedupe.Ledger().record("shorts", "Compound Interest Kya Hai Aur Yeh Kaise Kaam Karta Hai", status="built")
        self.write_queue([{"title": "Compound Interest Kya Hai? Samjho Asan Bhasha Mein"},
                          {"title": "Why ice floats on water"}])
        _, topic = topics.next_topic("shorts", self.queue)
        self.assertEqual(topic["title"], "Why ice floats on water")
        stored = json.loads(self.queue.read_text(encoding="utf-8"))
        self.assertTrue(stored[0]["skipped"])
        self.assertIn("duplicate", stored[0]["skip_reason"])

    def test_update_is_atomic_and_leaves_no_lock(self):
        self.write_queue([{"title": "One"}, {"title": "Two"}])
        self.assertTrue(topics.update_topic("Two", self.queue, done=True, youtube_id="x"))
        self.assertFalse(topics.update_topic("Missing", self.queue, done=True))
        stored = json.loads(self.queue.read_text(encoding="utf-8"))
        self.assertEqual(stored[1]["youtube_id"], "x")
        self.assertEqual(sorted(p.name for p in self.root.glob("topics.json*")), ["topics.json"])

    def test_stale_lock_is_recovered_and_live_lock_times_out(self):
        self.write_queue([])
        lock = Path(str(self.queue) + ".lock")
        lock.write_text("1")
        with self.assertRaises(TimeoutError):
            with topics.locked(self.queue, timeout=0.3):
                pass
        os.utime(lock, (1, 1))
        with topics.locked(self.queue, timeout=0.3):
            pass
        self.assertFalse(lock.exists())

    def test_invalid_queue_is_reported_not_overwritten(self):
        self.queue.write_text("[{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            topics.load_topics(self.queue)
        self.assertEqual(self.queue.read_text(encoding="utf-8"), "[{broken")

    def test_fresh_topic_comes_from_the_bank_and_never_repeats(self):
        bank = topics.bank_topics()
        self.assertGreaterEqual(len(bank), 30)
        with patch.object(topics, "TOPICS_PATH", self.queue):
            self.write_queue([])
            first = topics.pick_fresh_topic("shorts", use_model=False)
            dedupe.Ledger().record("shorts", first["topic"], status="built")
            second = topics.pick_fresh_topic("shorts", use_model=False)
        self.assertNotEqual(first["topic"], second["topic"])
        self.assertFalse(dedupe.is_same_subject(first["topic"], second["topic"]))

    def test_model_proposals_that_repeat_history_are_rejected(self):
        dedupe.Ledger().record("shorts", "How a transformer changes voltage", status="uploaded")
        answer = json.dumps({"topics": [{"topic": "How does a transformer change voltage", "category": "physics"},
                                        {"topic": "Why hot air balloons rise", "category": "physics"}]})
        with patch.object(topics, "TOPICS_PATH", self.queue), \
                patch("core.script_engine._ask_gemini", return_value=answer):
            self.write_queue([])
            choice = topics.pick_fresh_topic("shorts")
        self.assertEqual(choice["topic"], "Why hot air balloons rise")
        self.assertEqual(choice["source"], "model")


class RunDailyTests(_StateSandbox):
    def _run(self, argv, **mocks):
        import run_daily
        with patch.object(topics, "TOPICS_PATH", self.queue), patch("sys.argv", ["run_daily.py", *argv]):
            run_daily.main()

    def test_prewritten_topic_is_built_recorded_and_marked_done(self):
        import run_daily
        script = {"title": "Why magnets attract iron", "category": "physics",
                  "scenes": [{"say": "x", "caption": "x"}]}
        self.write_queue([script])
        with patch.object(run_daily, "save_video", return_value=41) as save, \
                patch.object(run_daily, "build_shorts", return_value="out.mp4") as build, \
                patch.object(run_daily, "send"):
            self._run(["--format", "shorts"])
        self.assertEqual(save.call_args.args[1], "shorts")
        build.assert_called_once()
        self.assertTrue(json.loads(self.queue.read_text(encoding="utf-8"))[0]["done"])
        self.assertIsNotNone(dedupe.Ledger().find("shorts", "Why magnets attract iron"))

    def test_long_format_is_stored_as_longform(self):
        import run_daily
        self.write_queue([{"title": "The story of the transistor", "format": "long",
                           "sections": [{"title": "a", "narration": "b"}]}])
        with patch.object(run_daily, "save_video", return_value=42) as save, \
                patch.object(run_daily, "build_longform", return_value="out.mp4"), \
                patch.object(run_daily, "send"):
            self._run(["--format", "long"])
        self.assertEqual(save.call_args.args[1], "longform")

    def test_empty_queue_without_a_model_notifies_and_builds_nothing(self):
        import run_daily
        self.write_queue([])
        with patch.object(run_daily, "_has_script_model", return_value=False), \
                patch.object(run_daily, "build_shorts") as build, patch.object(run_daily, "send") as send:
            self._run(["--format", "shorts"])
        build.assert_not_called()
        self.assertIn("No unused topics", send.call_args.args[0])

    def test_empty_queue_generates_a_new_topic_instead_of_repeating(self):
        import run_daily
        self.write_queue([])
        generated = {"title": "Bernoulli principle explained", "scenes": [{"say": "x"}]}
        with patch.object(run_daily, "_has_script_model", return_value=True), \
                patch.object(topics, "propose_topics", return_value=[]), \
                patch.object(run_daily, "generate_shorts_script", return_value=generated) as write, \
                patch.object(run_daily, "save_video", return_value=43), \
                patch.object(run_daily, "build_shorts", return_value="o.mp4"), patch.object(run_daily, "send"):
            self._run(["--format", "shorts"])
            first_topic = write.call_args.args[0]
            self._run(["--format", "shorts"])
            second_topic = write.call_args.args[0]
        self.assertNotEqual(first_topic, second_topic)

    def test_build_failure_is_counted_and_reported(self):
        import run_daily
        self.write_queue([{"title": "Why rockets need stages", "scenes": [{"say": "x"}]}])
        with patch.object(run_daily, "save_video", return_value=44), \
                patch.object(run_daily, "build_shorts", side_effect=RuntimeError("render failed")), \
                patch.object(run_daily, "send") as send:
            with self.assertRaises(RuntimeError):
                self._run(["--format", "shorts"])
        stored = json.loads(self.queue.read_text(encoding="utf-8"))[0]
        self.assertEqual(stored["attempts"], 1)
        self.assertNotIn("done", stored)
        self.assertIn("Build failed", send.call_args.args[0])


class NotifyTests(_StateSandbox):
    def test_without_channels_the_message_is_still_logged(self):
        with patch("core.notify.requests.post") as post:
            self.assertEqual(notify.send("✅ hello"), {})
        post.assert_not_called()
        line = json.loads((self.root / "logs" / "notifications.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((line["message"], line["level"]), ("✅ hello", "info"))

    def test_every_configured_channel_receives_the_message(self):
        ok = MagicMock(status_code=200, headers={})
        env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "9",
               "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x",
               "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/a/b/c",
               "NOTIFY_WEBHOOK_URL": "https://example.org/hook"}
        with patch.dict(os.environ, env), patch("core.notify.requests.post", return_value=ok) as post:
            delivered = notify.send("❌ render failed")
        self.assertEqual(delivered, {"telegram": True, "discord": True, "slack": True, "webhook": True})
        self.assertEqual(post.call_count, 4)

    def test_transient_errors_are_retried_and_never_raise(self):
        responses = [MagicMock(status_code=500, headers={}), MagicMock(status_code=200, headers={})]
        env = {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/secret"}
        with patch.dict(os.environ, env), patch("core.notify.time.sleep"), \
                patch("core.notify.requests.post", side_effect=responses) as post:
            self.assertTrue(notify.send("x")["discord"])
        self.assertEqual(post.call_count, 2)
        import requests
        with patch.dict(os.environ, env), patch("core.notify.time.sleep"), \
                patch("core.notify.requests.post", side_effect=requests.ConnectionError("down")):
            self.assertFalse(notify.send("x")["discord"])

    def test_secrets_are_redacted(self):
        self.assertNotIn("123:abcDEF", notify._safe("https://api.telegram.org/bot123:abcDEF/sendMessage"))
        self.assertNotIn("T0/B0/xyz", notify._safe("post to hooks.slack.com/services/T0/B0/xyz failed"))


def _candidate(title, publisher, publisher_url, hours=2, summary=""):
    stamp = format_datetime(datetime.now(timezone.utc) - timedelta(hours=hours))
    link = f"https://news.google.com/rss/articles/{abs(hash((title, publisher)))}"
    return {"id": f"id{abs(hash((title, publisher))) % 10**12}", "title": f"{title} - {publisher}", "url": link,
            "publisher": publisher, "publisher_url": publisher_url, "summary": summary, "published": stamp}


CORROBORATED = [
    _candidate("ISRO successfully tests reusable launch vehicle landing in Karnataka", "Reuters",
               "https://www.reuters.com", 3, "The space agency said the experiment met all 3 objectives."),
    _candidate("ISRO reusable launch vehicle completes autonomous landing test", "The Hindu",
               "https://www.thehindu.com", 4),
]


class NewsAutoTests(_StateSandbox):
    def test_corroborated_story_is_selected(self):
        story, audit = news_auto.select_story(CORROBORATED)
        self.assertIsNotNone(story)
        self.assertEqual({s["publisher"] for s in story["sources"]}, {"Reuters", "The Hindu"})
        self.assertTrue(audit[0]["eligible"])

    def test_single_or_untrusted_sources_are_rejected(self):
        single = [CORROBORATED[0]]
        self.assertIsNone(news_auto.select_story(single)[0])
        untrusted = [_candidate("Miracle battery lasts forever says startup", "Viral Daily", "https://viral.example"),
                     _candidate("Miracle battery lasts forever startup says", "Gossip Hub", "https://gossip.example")]
        self.assertIsNone(news_auto.select_story(untrusted)[0])

    def test_stale_reports_are_rejected(self):
        old = [dict(item, published=format_datetime(datetime.now(timezone.utc) - timedelta(days=5)))
               for item in CORROBORATED]
        self.assertIsNone(news_auto.select_story(old)[0])

    def test_sensitive_stories_are_left_for_humans(self):
        sensitive = [_candidate("Court grants bail to minister accused in land scam", "Reuters", "https://www.reuters.com"),
                     _candidate("Minister accused in land scam gets bail from court", "The Hindu", "https://www.thehindu.com")]
        story, audit = news_auto.select_story(sensitive)
        self.assertIsNone(story)
        self.assertIn("sensitive", audit[0]["reason"])
        # A publisher's own name must not trip the filter.
        self.assertIsNone(news_auto.is_sensitive("Report by The Hindu on rockets", ignore=["The Hindu"]))

    def test_story_already_covered_is_not_selected_again(self):
        story, _ = news_auto.select_story(CORROBORATED)
        dedupe.Ledger().record("news_short", story["headline"], source_refs=story["source_refs"])
        self.assertIsNone(news_auto.select_story(CORROBORATED)[0])

    def test_template_script_is_grounded_and_invented_numbers_are_rejected(self):
        story, _ = news_auto.select_story(CORROBORATED)
        script = news_auto.template_script(story)
        self.assertEqual(news_auto.validate_news_script(script, story), [])
        script["scenes"][2]["say"] = "The mission cost 500 crore rupees according to officials familiar with it."
        problems = " ".join(news_auto.validate_news_script(script, story))
        self.assertIn("500", problems)
        unattributed = news_auto.template_script(story)
        for scene in unattributed["scenes"]:
            scene["say"] = "A rocket landed on a runway today and everyone was very happy about it."
        self.assertTrue(any("attribut" in p or "publisher" in p
                            for p in news_auto.validate_news_script(unattributed, story)))

    def test_ungrounded_model_output_fails_closed(self):
        story, _ = news_auto.select_story(CORROBORATED)
        bad = json.dumps({"title": "Rocket", "scenes": [
            {"say": f"Shocking news number {i}, the rocket cost 900 crore and exploded twice",
             "caption": "c", "label": "l", "image": "i", "keywords": ["k"]} for i in range(4)]})
        with patch("core.script_engine._ask_gemini", return_value=bad), \
                patch.dict(os.environ, {"NEWS_TEMPLATE_FALLBACK": ""}):
            with self.assertRaises(news_auto.NoPublishableStory):
                news_auto.build_script(story, "english")

    def test_run_without_an_eligible_story_builds_nothing(self):
        with patch("core.pipeline.build_shorts") as build:
            result = news_auto.run(candidates=[CORROBORATED[0]])
        self.assertEqual(result["status"], "no_story")
        build.assert_not_called()
        self.assertTrue((self.root / "output" / "daily_news" / "auto_selection.json").is_file())

    def test_run_builds_records_and_marks_story_seen(self):
        with patch("core.db.save_video", return_value=77), \
                patch("core.pipeline.build_shorts", return_value="news.mp4") as build, \
                patch.dict(os.environ, {"NEWS_TEMPLATE_FALLBACK": "1"}), \
                patch("core.script_engine._ask_gemini", side_effect=RuntimeError("no key")):
            result = news_auto.run(candidates=list(CORROBORATED))
            self.assertEqual(result["status"], "built")
            script = build.call_args.args[0]
            self.assertEqual(script["format"], "news_short")
            self.assertEqual(len(script["sources"]), 2)
            verification = json.loads((self.root / "build" / "job_77" / "verification.json").read_text())
            self.assertEqual(verification["status"], "PASS")
            # Second run: same feed items are now seen/covered.
            self.assertEqual(news_auto.run(candidates=list(CORROBORATED))["status"], "no_story")
        self.assertEqual(build.call_count, 1)

    def test_rss_parser_keeps_real_publisher_address(self):
        from core import daily_news_pipeline
        feed = (b'<rss><channel><item><title>Rocket lands - Reuters</title>'
                b'<link>https://news.google.com/rss/articles/abc</link>'
                b'<description>&lt;a href="x"&gt;Rocket lands&lt;/a&gt;&amp;nbsp;Reuters</description>'
                b'<pubDate>Thu, 12 Nov 2026 08:00:00 GMT</pubDate>'
                b'<source url="https://www.reuters.com">Reuters</source></item></channel></rss>')
        response = MagicMock(content=feed)
        with patch("core.daily_news_pipeline.requests.get", return_value=response):
            rows = daily_news_pipeline.collect_feed("https://news.google.com/rss")
        self.assertEqual(rows[0]["publisher_url"], "https://www.reuters.com")
        self.assertNotIn("<", rows[0]["summary"])


@unittest.skipUnless(shutil.which("git"), "git is required")
class PersistStateTests(unittest.TestCase):
    def test_ledgers_merge_without_losing_uploads(self):
        from scripts import persist_state
        ours = {"version": 1, "entries": [{"kind": "shorts", "key": "a", "title": "A", "status": "built",
                                           "updated_at": "2026-01-02"}]}
        theirs = {"version": 1, "entries": [
            {"kind": "shorts", "key": "a", "title": "A", "status": "uploaded", "youtube_id": "yt1",
             "updated_at": "2026-01-01"},
            {"kind": "shorts", "key": "b", "title": "B", "updated_at": "2026-01-01"}]}
        merged = {e["key"]: e for e in persist_state.merge_ledgers(ours, theirs)["entries"]}
        self.assertEqual(set(merged), {"a", "b"})
        self.assertEqual(merged["a"]["youtube_id"], "yt1")  # proof of upload survives

    def test_seen_caches_are_unioned(self):
        from scripts import persist_state
        merged = persist_state._merge_seen({"ids": ["b", "c"], "updated_at": "2"}, {"ids": ["a", "b"], "updated_at": "1"})
        self.assertEqual(merged["ids"], ["a", "b", "c"])

    def test_state_round_trips_through_a_dedicated_branch(self):
        from scripts import persist_state
        git = lambda *args, cwd: subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            git("init", "--bare", "-q", "remote.git", cwd=tmp)
            for name in ("a", "b"):
                git("clone", "-q", "remote.git", name, cwd=tmp)
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=tmp / "a")
            git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=tmp / "a")

            def act(repo, state, fn, *args):
                with patch.object(persist_state, "ROOT", tmp / repo), \
                        patch.dict(os.environ, {"AI_STUDIO_STATE_DIR": str(tmp / state)}):
                    return fn(*args)
            (tmp / "sa").mkdir(); (tmp / "sb").mkdir(); (tmp / "sc").mkdir()
            entry = lambda key: {"kind": "shorts", "key": key, "title": key, "updated_at": "2026-01-01"}
            (tmp / "sa" / "publish_ledger.json").write_text(json.dumps({"version": 1, "entries": [entry("k1")]}))
            (tmp / "sa" / "secret.txt").write_text("must never be pushed")
            self.assertTrue(act("a", "sa", persist_state.save))
            self.assertFalse(act("a", "sa", persist_state.save))            # nothing new
            self.assertEqual(act("b", "sb", persist_state.restore), 1)
            (tmp / "sb" / "publish_ledger.json").write_text(
                json.dumps({"version": 1, "entries": [entry("k1"), entry("k2")]}))
            (tmp / "sa" / "publish_ledger.json").write_text(
                json.dumps({"version": 1, "entries": [entry("k1"), entry("k3")]}))
            act("b", "sb", persist_state.save)
            act("a", "sa", persist_state.save)                               # concurrent writers merge
            act("b", "sc", persist_state.restore)
            keys = {e["key"] for e in json.loads((tmp / "sc" / "publish_ledger.json").read_text())["entries"]}
            self.assertEqual(keys, {"k1", "k2", "k3"})
            self.assertFalse((tmp / "sc" / "secret.txt").exists())
            branches = subprocess.run(["git", "branch", "--list"], cwd=tmp / "remote.git",
                                      capture_output=True, text=True).stdout
            self.assertIn("automation-state", branches)


class WorkflowHygieneTests(unittest.TestCase):
    WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

    def _run_blocks(self, text):
        """Yield the text of every ``run:`` block scalar (indentation based, no YAML dependency)."""
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            match = re.match(r"^(\s*)(?:- )?run:\s*[|>][+-]?\s*$", lines[index])
            if not match:
                single = re.match(r"^\s*(?:- )?run:\s*(\S.*)$", lines[index])
                if single:
                    yield single.group(1)
                index += 1
                continue
            indent = len(match.group(1))
            block = []
            index += 1
            while index < len(lines) and (not lines[index].strip()
                                          or len(lines[index]) - len(lines[index].lstrip()) > indent):
                block.append(lines[index])
                index += 1
            yield "\n".join(block)

    def test_user_inputs_are_never_interpolated_into_shell(self):
        offenders = []
        for path in self.WORKFLOWS:
            for block in self._run_blocks(path.read_text(encoding="utf-8")):
                if re.search(r"\$\{\{\s*(inputs|github\.event\.(inputs|issue|comment|pull_request|head_commit))\b", block):
                    offenders.append(path.name)
        self.assertEqual(sorted(set(offenders)), [], "Pass inputs through env: instead of ${{ }} in run:")

    def test_no_doubled_backslash_line_continuations(self):
        offenders = [path.name for path in self.WORKFLOWS
                     for block in self._run_blocks(path.read_text(encoding="utf-8"))
                     if re.search(r"\\\\\s*$", block, flags=re.M)]
        self.assertEqual(offenders, [])

    def test_workflows_parse_and_scheduled_ones_are_serialised(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for path in self.WORKFLOWS:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIn("jobs", data, path.name)
            triggers = data.get(True) or data.get("on") or {}
            if isinstance(triggers, dict) and "schedule" in triggers:
                self.assertIn("concurrency", data, f"{path.name} is scheduled but not serialised")

    def test_automatic_workflows_upload_privately_and_never_push_to_main(self):
        for name in ("make_shorts.yml", "daily-news-auto-short.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertNotIn("ALLOW_PUBLIC_PUBLISH", text)
            self.assertNotIn("HEAD:main", text)
            self.assertIn("persist_state.py restore", text)
            self.assertIn("persist_state.py save", text)


class HealthCheckTests(_StateSandbox):
    def test_report_is_structured_and_never_prints_secret_values(self):
        from core import health
        secret = "sk-test-SECRET-VALUE-123456"
        with patch.dict(os.environ, {"GROQ_API_KEY": secret, "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/" + secret}):
            report = health.run_health_check()
        self.assertIn(report["status"], {"PASS", "WARN", "FAIL"})
        names = {check["check"] for check in report["checks"]}
        self.assertTrue({"ffmpeg", "database", "upload privacy", "YouTube channel lock"} <= names)
        self.assertNotIn(secret, json.dumps(report))


if __name__ == "__main__":
    unittest.main()
