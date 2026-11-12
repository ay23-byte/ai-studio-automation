"""Unit tests for the shared platform modules added during the production audit."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import (config, content_qa, dedupe, explainer_cards, ffmpeg_utils, fonts, jobs, llm,
                  render_join, subtitles_export, timing)
from core import documentary_storyboard as storyboard
from core.subtitle_translation import needs_translation, normalize_subtitle_language

ROOT = Path(__file__).resolve().parents[1]


class ConfigSandboxTests(unittest.TestCase):
    def test_test_runs_never_use_the_repository_database(self):
        self.assertTrue(config._running_under_test_runner())
        self.assertNotEqual(Path(config.DB_PATH).resolve(), (ROOT / "db" / "studio.sqlite").resolve())

    def test_unittest_argv_rewrite_is_detected(self):
        with patch("sys.argv", ["python3 -m unittest", "discover"]), \
                patch.dict(os.environ, {}, clear=True):
            self.assertTrue(config._running_under_test_runner())
        with patch("sys.argv", ["run.py", "shorts"]), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(config._running_under_test_runner())

    def test_env_flag(self):
        with patch.dict(os.environ, {"X_FLAG": "Yes"}):
            self.assertTrue(config.env_flag("X_FLAG"))
        with patch.dict(os.environ, {"X_FLAG": "0"}):
            self.assertFalse(config.env_flag("X_FLAG"))


class FfmpegUtilsTests(unittest.TestCase):
    def test_windows_paths_are_escaped_for_filter_arguments(self):
        escaped = ffmpeg_utils.filter_path(r"C:\Users\A B\job's\subs.ass")
        self.assertNotIn("\\U", escaped)          # backslashes normalised
        self.assertIn("C\\:", escaped)             # drive colon escaped
        self.assertNotIn("job's", escaped)         # quote cannot terminate the argument

    def test_subtitles_filter_points_libass_at_bundled_fonts(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = ffmpeg_utils.subtitles_filter("/tmp/x/subs.ass", fonts_dir=tmp)
            self.assertTrue(value.startswith("subtitles="))
            self.assertIn("fontsdir=", value)
        # Without an explicit directory the bundled fonts are used.
        self.assertIn("fontsdir=", ffmpeg_utils.subtitles_filter("/tmp/x/subs.ass"))

    def test_concat_chain_labels_every_input(self):
        chain = ffmpeg_utils.concat_chain(3, "vcat")
        self.assertIn("[0:v][1:v][2:v]concat=n=3", chain)
        self.assertTrue(chain.endswith("[vcat]"))

    def test_missing_binary_is_reported_not_raised(self):
        with patch.dict(os.environ, {"FFMPEG_BINARY": "definitely-not-ffmpeg-xyz"}):
            self.assertFalse(ffmpeg_utils.tools_available()["ffmpeg"])
            with self.assertRaises(ffmpeg_utils.FFmpegUnavailable):
                ffmpeg_utils.require_tools()


class RenderJoinTests(unittest.TestCase):
    def test_batch_size_is_bounded(self):
        with patch.dict(os.environ, {"RENDER_JOIN_BATCH": "1"}):
            self.assertGreaterEqual(render_join.batch_size(), 2)
        with patch.dict(os.environ, {"RENDER_JOIN_BATCH": "not-a-number"}):
            self.assertEqual(render_join.batch_size(), 16)

    def test_video_chain_without_xfade_uses_plain_concat(self):
        with patch("core.ffmpeg_utils.xfade_supported", return_value=False):
            chain = render_join.video_chain([2.0, 3.0, 4.0], ["fade", "fade"], 0.3)
        self.assertNotIn("xfade", chain)
        self.assertIn("concat", chain)

    def test_video_chain_with_xfade_offsets_accumulate(self):
        with patch("core.ffmpeg_utils.xfade_supported", return_value=True):
            chain = render_join.video_chain([2.0, 3.0, 4.0], ["fade", "wipeleft"], 0.5)
        self.assertEqual(chain.count("xfade="), 2)
        # Durations are the visible lengths (the overlap is already in each segment).
        self.assertIn("offset=2.000", chain)
        self.assertIn("offset=5.000", chain)
        self.assertTrue(chain.endswith("[vjoin]"))


class FontTests(unittest.TestCase):
    def test_devanagari_detection(self):
        self.assertTrue(fonts.contains_devanagari("यह हिंदी है"))
        self.assertFalse(fonts.contains_devanagari("Yeh Hinglish hai"))

    def test_bundled_fonts_cover_both_scripts(self):
        self.assertTrue((ROOT / "assets" / "fonts" / "NotoSansDevanagari-Regular.ttf").is_file())
        self.assertIn("Devanagari", Path(fonts.font_path("नमस्ते")).name)
        self.assertTrue(Path(fonts.font_path("Hello")).is_file())
        self.assertIn("Devanagari", fonts.ass_font_name("नमस्ते"))

    def test_load_font_never_raises(self):
        self.assertIsNotNone(fonts.load_font(40, bold=True, text="नमस्ते"))


class TimingTests(unittest.TestCase):
    def test_spans_are_contiguous_and_cover_the_track(self):
        texts = ["one two three", "four five", "six seven eight nine"]
        timings = [(0.0, 2.0, "one two three"), (2.0, 3.5, "four five"), (3.5, 7.0, "six seven eight nine")]
        spans = timing.align_units(texts, timings, 7.0)
        self.assertEqual(len(spans), 3)
        self.assertAlmostEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[-1][1], 7.0)
        for left, right in zip(spans, spans[1:]):
            self.assertAlmostEqual(left[1], right[0])
        self.assertAlmostEqual(spans[0][1], 2.0, delta=0.4)

    def test_unusable_timings_fall_back_to_proportional_split(self):
        spans = timing.align_units(["aaaa", "bbbb"], [("bad",)], 10.0)
        self.assertAlmostEqual(spans[0][1], 5.0, delta=0.6)
        self.assertEqual(timing.align_units([], [], 5.0), [])


class SubtitleExportTests(unittest.TestCase):
    TIMINGS = [(0.0, 1.5, "Hello world"), (1.5, 3.25, "Second line")]

    def test_srt_and_vtt_formats(self):
        srt = subtitles_export.to_srt(self.TIMINGS)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\nHello world", srt)
        vtt = subtitles_export.to_vtt(self.TIMINGS)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:01.500 --> 00:00:03.250", vtt)

    def test_long_cues_are_split(self):
        cues = subtitles_export.split_cues([(0.0, 12.0, "word " * 60)], max_chars=60, max_seconds=4.0)
        self.assertGreater(len(cues), 2)
        self.assertAlmostEqual(cues[-1][1], 12.0, delta=0.01)

    def test_sidecars_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = subtitles_export.write_sidecars(self.TIMINGS, tmp, language="en")
            self.assertTrue(Path(written["srt"]).is_file())
            self.assertTrue(Path(written["vtt"]).is_file())
            self.assertEqual(written["cue_count"], 2)


class CheckpointTests(unittest.TestCase):
    def test_completed_stage_is_reused_and_invalidated_by_new_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            checkpoint = jobs.Checkpoint(901, Path(tmp), mirror_to_db=False)
            run = lambda: calls.append(1) or {"value": len(calls)}
            first = checkpoint.run("voice", run, input_hash="a")
            again = jobs.Checkpoint(901, Path(tmp), mirror_to_db=False).run("voice", run, input_hash="a")
            self.assertEqual((first, again, len(calls)), ({"value": 1}, {"value": 1}, 1))
            jobs.Checkpoint(901, Path(tmp), mirror_to_db=False).run("voice", run, input_hash="b")
            self.assertEqual(len(calls), 2)

    def test_missing_output_file_forces_a_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "voice.wav"
            calls = []

            def produce():
                calls.append(1)
                artifact.write_bytes(b"x" * 10)
                return str(artifact)
            checkpoint = jobs.Checkpoint(902, Path(tmp), mirror_to_db=False)
            checkpoint.run("voice", produce, input_hash="a", files=[artifact])
            artifact.unlink()
            checkpoint.run("voice", produce, input_hash="a", files=[artifact])
            self.assertEqual(len(calls), 2)

    def test_failure_is_recorded_and_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = jobs.Checkpoint(903, Path(tmp), mirror_to_db=False)
            with self.assertRaises(RuntimeError):
                checkpoint.run("render", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
            record = checkpoint.get("render")
            self.assertEqual(record["status"], "failed")
            self.assertIn("boom", record["error"])
            self.assertEqual(checkpoint.run("render", lambda: "ok"), "ok")
            self.assertEqual(jobs.load_checkpoint_summary(tmp)[-1]["status"], "done")

    def test_resume_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"AI_STUDIO_RESUME": "0"}):
            calls = []
            checkpoint = jobs.Checkpoint(904, Path(tmp), mirror_to_db=False)
            checkpoint.run("s", lambda: calls.append(1))
            checkpoint.run("s", lambda: calls.append(1))
            self.assertEqual(len(calls), 2)


class DedupeTests(unittest.TestCase):
    def test_reworded_titles_are_the_same_subject(self):
        self.assertTrue(dedupe.is_same_subject("Compound Interest Kya Hai Aur Yeh Kaise Kaam Karta Hai",
                                               "Compound Interest Kya Hai? Samjho Asan Bhasha Mein"))
        self.assertFalse(dedupe.is_same_subject("How a transformer changes voltage",
                                                "Why the sky is blue"))
        self.assertEqual(dedupe.topic_key("Compound  Interest!"), dedupe.topic_key("compound interest"))

    def test_ledger_round_trip_and_duplicate_lookup(self):
        with tempfile.TemporaryDirectory() as tmp, patch("core.dedupe.known_titles_from_db", return_value=[]):
            ledger = dedupe.Ledger(Path(tmp) / "ledger.json")
            ledger.record("shorts", "What is the Doppler effect", job_id=7, status="built")
            reloaded = dedupe.Ledger(Path(tmp) / "ledger.json")
            hit = dedupe.find_duplicate("shorts", "Doppler effect kya hai", ledger=reloaded)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["layer"], "ledger")
            self.assertIsNone(dedupe.find_duplicate("longform", "What is the Doppler effect", ledger=reloaded))
            self.assertIsNone(dedupe.find_duplicate("shorts", "How lasers work", ledger=reloaded))

    def test_story_ids_match_even_when_headlines_differ(self):
        with tempfile.TemporaryDirectory() as tmp, patch("core.dedupe.known_titles_from_db", return_value=[]):
            ledger = dedupe.Ledger(Path(tmp) / "ledger.json")
            ledger.record("news_short", "Rocket test succeeds", source_refs=["abc123"])
            self.assertIsNotNone(dedupe.find_duplicate(
                "news_short", "A totally different headline", source_refs=["abc123"], ledger=ledger))

    def test_corrupt_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                dedupe.Ledger(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")  # never overwritten

    def test_upload_marker_is_stable(self):
        marker = dedupe.upload_marker("shorts", "key")
        self.assertTrue(marker.startswith("AI_STUDIO_DEDUPE:shorts:"))
        self.assertEqual(marker, dedupe.upload_marker("shorts", "key"))


def _script(words_per_scene=14, scenes=5, say=None):
    def body(index):
        text = say or " ".join(["energy"] * (words_per_scene - 2))
        return f"{text} part {['one', 'two', 'three', 'four', 'five', 'six'][index % 6]}"
    return {"title": "How energy moves", "scenes": [
        {"say": body(i), "caption": body(i), "label": "Energy", "image": "a glowing coil", "keywords": ["coil"]}
        for i in range(scenes)]}


class ContentQaTests(unittest.TestCase):
    def test_well_formed_script_is_accepted(self):
        self.assertNotEqual(content_qa.validate_shorts_script(_script(), "english")["status"], "FAIL")

    def test_structural_problems_fail(self):
        self.assertEqual(content_qa.validate_shorts_script({"title": "x"}, "english")["status"], "FAIL")
        self.assertEqual(content_qa.validate_shorts_script(_script(scenes=1), "english")["status"], "FAIL")
        self.assertEqual(content_qa.validate_shorts_script(_script(words_per_scene=80), "english")["status"], "FAIL")

    def test_risky_claims_fail(self):
        report = content_qa.validate_shorts_script(
            _script(say="This plan gives guaranteed returns and you will double your money every single year"),
            "english")
        self.assertEqual(report["status"], "FAIL")
        with self.assertRaises(ValueError):
            content_qa.assert_shorts_script({"scenes": []})

    def test_wrong_script_for_language_fails(self):
        hindi = _script(say="यह ऊर्जा के बारे में एक सरल और साफ़ व्याख्या है जो सबको समझ आएगी")
        self.assertEqual(content_qa.validate_shorts_script(hindi, "english")["status"], "FAIL")
        self.assertNotEqual(content_qa.validate_shorts_script(hindi, "hindi")["status"], "FAIL")


class LlmFallbackTests(unittest.TestCase):
    def test_only_providers_with_keys_are_configured_and_ordered(self):
        env = {"GROQ_API_KEY": "g", "OPENROUTER_API_KEY": "o", "LLM_PROVIDER_ORDER": "openrouter,groq"}
        with patch.dict(os.environ, env, clear=True):
            names = [p["name"] for p in llm.configured_providers()]
        self.assertEqual(names, ["openrouter", "groq"])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.configured_providers(), [])

    def test_next_provider_is_used_when_the_first_fails(self):
        providers = [{"name": "a", "model": "m", "api_key": "k", "base_url": "http://a"},
                     {"name": "b", "model": "m", "api_key": "k", "base_url": "http://b"}]

        def fake(config, prompt, **kwargs):
            if config["name"] == "a":
                raise llm.LLMError("a", "HTTP 429")
            return '{"ok": true}'
        with patch("core.llm.complete", side_effect=fake):
            self.assertEqual(llm.complete_with_fallbacks("p", providers), '{"ok": true}')
        with patch("core.llm.complete", side_effect=llm.LLMError("x", "down")):
            with self.assertRaises(llm.LLMError):
                llm.complete_with_fallbacks("p", providers)

    def test_keys_are_redacted_from_errors(self):
        self.assertNotIn("sk-abcdef1234567890abcd", llm.redact("Bearer sk-abcdef1234567890abcd failed"))


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_database_is_migrated_without_losing_rows(self):
        from core import db
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "studio.sqlite"
            conn = sqlite3.connect(legacy)
            conn.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, format TEXT,"
                         " language TEXT, category TEXT, script_json TEXT, video_path TEXT, thumb_path TEXT,"
                         " youtube_id TEXT, status TEXT DEFAULT 'draft', notes TEXT,"
                         " created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO videos (title, format, status, youtube_id) VALUES ('Old', 'long', 'uploaded', 'abc')")
            conn.commit(); conn.close()
            with patch.object(db, "DB_PATH", legacy):
                db.init_db()
                rows = db.list_videos()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["youtube_id"], "abc")
                self.assertEqual(db.integrity_check(), "ok")
                self.assertTrue(list((Path(tmp) / "backups").glob("*.sqlite")))
                new_id = db.save_video("New", "long", "english", "physics", {"a": 1})
                self.assertEqual(db.get_video(new_id)["format"], "longform")
                with self.assertRaises(ValueError):
                    db.update_video(new_id, **{"title = 'x'; --": 1})
                db.update_video(new_id, status="done")
                self.assertEqual(db.get_video(new_id)["status"], "done")
                db.init_db()  # idempotent
                self.assertEqual(len(db.list_videos()), 2)

    def test_failed_transaction_rolls_back(self):
        from core import db
        with tempfile.TemporaryDirectory() as tmp, patch.object(db, "DB_PATH", Path(tmp) / "s.sqlite"):
            db.init_db()
            with self.assertRaises(RuntimeError):
                with db.transaction() as conn:
                    conn.execute("INSERT INTO videos (title, format) VALUES ('temp', 'shorts')")
                    raise RuntimeError("abort")
            self.assertEqual(db.list_videos(), [])


class ExplainerCardTests(unittest.TestCase):
    def test_card_is_rendered_with_provenance_and_no_placeholder_text(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "card.jpg"
            explainer_cards.render_card(path, "Voltage changes because the coil turns differ", size=(1920, 1080),
                                        kicker="Chapter 1", index=1, total=4, keywords=["documentary subject"])
            self.assertEqual(Image.open(path).size, (1920, 1080))
            self.assertTrue(explainer_cards.is_card(path))
            sidecar = json.loads(Path(str(path) + ".source.json").read_text(encoding="utf-8"))
            self.assertFalse(sidecar.get("ai_generated"))
            self.assertNotIn("documentary subject", json.dumps(sidecar).lower())

    def test_fallback_share_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            cards = []
            for index in range(3):
                path = Path(tmp) / f"card{index}.jpg"
                explainer_cards.render_card(path, f"Idea number {index}", index=index, total=4)
                cards.append(path)
            real = Path(tmp) / "photo.jpg"
            real.write_bytes(b"x")
            report = explainer_cards.write_fallback_report(tmp, cards + [real], 0.5)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["fallback_card_count"], 3)
            self.assertEqual(explainer_cards.write_fallback_report(tmp, [real, real, real, cards[0]], 0.5)["status"],
                             "REVIEW")


class StoryboardVisualTypeTests(unittest.TestCase):
    def test_photograph_is_not_a_graph(self):
        self.assertEqual(storyboard.infer_visual_type({"visual_prompt": "An archival photograph of the reactor"}),
                         "archival")
        self.assertEqual(storyboard.infer_visual_type({"visual_prompt": "A product roadmap on a whiteboard"}),
                         "stock")

    def test_plurals_still_match(self):
        self.assertEqual(storyboard.infer_visual_type({"visual_prompt": "Animated charts of output"}), "chart")
        self.assertEqual(storyboard.infer_visual_type({"visual_prompt": "Two maps of the region"}), "map")
        self.assertEqual(storyboard.infer_visual_type({"visual_type": "timeline"}), "timeline")


class SubtitleLanguageTests(unittest.TestCase):
    def test_same_language_captions_skip_the_model(self):
        self.assertFalse(needs_translation(["Plain English narration."], "english", "english"))
        self.assertFalse(needs_translation(["anything"], "source", "hindi"))
        self.assertTrue(needs_translation(["यह हिंदी है"], "english", "hindi"))
        self.assertTrue(needs_translation(["Yeh Hinglish hai"], "english", "hinglish"))
        self.assertTrue(needs_translation(["text"], "english", None))

    def test_language_names_are_normalised(self):
        self.assertEqual(normalize_subtitle_language("EN"), "english")
        self.assertEqual(normalize_subtitle_language("nonsense"), "english")


class ShortsAssetManifestTests(unittest.TestCase):
    """Every Short records where each visual came from."""

    def _asset(self, root, name, metadata=None):
        from PIL import Image
        path = Path(root) / name
        Image.new("RGB", (64, 64), (20, 30, 40)).save(path)
        if metadata is not None:
            Path(str(path) + ".source.json").write_text(json.dumps(metadata), encoding="utf-8")
        return path

    def test_manifest_records_provenance_cards_and_synthetic_media(self):
        from core import asset_manifest
        with tempfile.TemporaryDirectory() as tmp:
            stock = self._asset(tmp, "img0.jpg", {
                "provider": "Pixabay", "asset_type": "stock_photo", "licence": "Pixabay Content License",
                "licence_url": "https://pixabay.com/service/license-summary/",
                "page_url": "https://pixabay.com/photos/example-1/", "user": "someone"})
            generated = self._asset(tmp, "img1.jpg", {
                "provider": "Pollinations", "asset_type": "ai_generated_image", "ai_generated": True})
            card = explainer_cards.render_card(Path(tmp) / "card2.jpg", "Voltage scales with turns",
                                               kicker="Induction", index=2, total=4)
            unknown = self._asset(tmp, "img3.jpg")
            media = [{"path": str(stock), "type": "image", "provider": "pixabay"},
                     {"path": str(generated), "type": "image", "provider": "pollinations"},
                     {"path": str(card), "type": "image", "provider": "card"},
                     {"path": str(unknown), "type": "image"}]
            scenes = [{"say": f"Scene {i}."} for i in range(4)]
            manifest = asset_manifest.write_shorts_asset_manifest(tmp, scenes, media)
            saved = json.loads((Path(tmp) / "asset_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((Path(tmp) / "licenses.csv").is_file())
        self.assertEqual(saved["asset_count"], 4)
        self.assertEqual(manifest["ai_generated_count"], 1)
        self.assertEqual(manifest["fallback_card_count"], 1)
        self.assertEqual(manifest["without_provenance"], ["img3.jpg"])
        self.assertTrue(manifest["contains_synthetic_media"])
        self.assertEqual(saved["assets"][0]["source_url"], "https://pixabay.com/photos/example-1/")
        self.assertEqual(saved["assets"][0]["narration"], "Scene 0.")
        self.assertGreaterEqual(saved["licence_register"]["blocked_count"], 1)   # the unknown asset

    def test_manifest_without_synthetic_assets(self):
        from core import asset_manifest
        with tempfile.TemporaryDirectory() as tmp:
            card = explainer_cards.render_card(Path(tmp) / "card0.jpg", "Two coils share one core", index=0, total=1)
            manifest = asset_manifest.write_shorts_asset_manifest(
                tmp, [{"say": "Two coils."}], [{"path": str(card), "type": "image", "provider": "card"}])
        self.assertFalse(manifest["contains_synthetic_media"])
        self.assertEqual(manifest["without_provenance"], [])
        self.assertEqual(manifest["licence_register"]["blocked_count"], 0)


class ChapterTimelineBudgetTests(unittest.TestCase):
    """A chapter's shots always add up to that chapter's narration length."""

    def test_slate_and_visuals_fill_what_the_presenter_left(self):
        from core import pipeline
        # 21 s chapter, 3.4 s presenter intro already placed, three planned visuals.
        title, visuals = pipeline._slate_budget(21.0, 3.4, [6.0, 6.0, 5.6])
        self.assertAlmostEqual(title, 2.4)
        self.assertAlmostEqual(3.4 + title + sum(visuals), 21.0, places=6)
        self.assertAlmostEqual(visuals[0] / visuals[2], 6.0 / 5.6, places=6)   # proportions kept

    def test_without_presenter_the_chapter_is_still_exact(self):
        from core import pipeline
        title, visuals = pipeline._slate_budget(40.0, 0.0, [10.0, 20.0, 10.0])
        self.assertAlmostEqual(title + sum(visuals), 40.0, places=6)

    def test_no_slate_when_there_is_no_room(self):
        from core import pipeline
        self.assertEqual(pipeline._slate_budget(8.0, 0.0, [4.0, 4.0]), (0.0, [4.0, 4.0]))     # chapter too short
        self.assertEqual(pipeline._slate_budget(30.0, 28.0, [1.0, 1.0]), (0.0, [1.0, 1.0]))   # presenter filled it
        self.assertEqual(pipeline._slate_budget(30.0, 0.0, []), (0.0, []))

    def test_drift_is_measured_per_chapter(self):
        from core import pipeline
        sections = [{"_dur": 10.0}, {"_dur": 12.0}]
        exact = [{"_chapter_index": 0, "_dur": 4.0}, {"_chapter_index": 0, "_dur": 6.0},
                 {"_chapter_index": 1, "_dur": 12.0}]
        self.assertAlmostEqual(pipeline._timeline_drift(exact, sections), 0.0)
        overrun = exact + [{"_chapter_index": 1, "_dur": 3.4}]
        self.assertAlmostEqual(pipeline._timeline_drift(overrun, sections), 3.4)


class LongformCaptionLayoutTests(unittest.TestCase):
    """Overlays are held back while a full-screen chapter slate is on screen."""

    def test_visible_spans_subtract_slates(self):
        from core import pipeline
        self.assertEqual(pipeline._visible_spans(30, []), [(0.0, 30.0)])
        self.assertEqual(pipeline._visible_spans(30, [(0, 2.4), (10, 12)]), [(2.4, 10.0), (12.0, 30.0)])
        self.assertEqual(pipeline._visible_spans(30, [(28, 40)]), [(0.0, 28.0)])

    def test_slate_window_hides_watermark_and_duplicate_heading(self):
        from core import pipeline
        sections = [{"title": "Why the grid hums", "_start": 0.0}, {"title": "Frequency control", "_start": 10.0}]
        timings = [(0.0, 5.0, "The grid hums at fifty hertz."), (10.0, 15.0, "Generators share the load.")]
        with tempfile.TemporaryDirectory() as tmp:
            ass = Path(tmp) / "subs.ass"
            pipeline._make_ass_longform(timings, sections, 20.0, ass, True, quiet_windows=[(0.0, 2.4)])
            events = [line for line in ass.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
        marks = [line for line in events if ",Mark," in line]
        self.assertEqual(len(marks), 1)
        self.assertIn("0:00:02.40", marks[0])               # watermark starts after the slate
        lowers = [line for line in events if ",Lower," in line]
        self.assertEqual(len(lowers), 1)                     # the slate replaces the first heading
        self.assertIn("Frequency control", lowers[0])
        self.assertEqual(len([line for line in events if ",Disc," in line]), 1)

    def test_without_slates_layout_is_unchanged(self):
        from core import pipeline
        sections = [{"title": "Why the grid hums", "_start": 0.0}]
        with tempfile.TemporaryDirectory() as tmp:
            ass = Path(tmp) / "subs.ass"
            pipeline._make_ass_longform([(0.0, 5.0, "The grid hums.")], sections, 8.0, ass, False)
            text = ass.read_text(encoding="utf-8")
        self.assertEqual(text.count(",Mark,"), 1)
        self.assertEqual(text.count(",Lower,"), 1)
        self.assertNotIn(",Disc,,", text)


if __name__ == "__main__":
    unittest.main()
