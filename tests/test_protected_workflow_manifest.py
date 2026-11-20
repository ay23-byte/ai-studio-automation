"""Milestone 1 regression tests for the protected production surface.

These tests read a manifest generated from the audited baseline commit.  They
must stay dependency-free: ``daily-news-shorts.yml`` installs only requests and
Pillow, and protected-workflow integrity must not depend on optional packages.

A failure here means either a protected file changed or the manifest was
regenerated without an explicit, reviewed decision.  Neither case should be
silently accepted as "CI is green".
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "audit" / "protected-workflow-manifest.json"
GENERATOR_PATH = ROOT / "scripts" / "audit" / "generate_protected_manifest.py"

EXPECTED_BASELINE = "bdb6f82180a22e5ad7532372c3cbd1dc531d95c9"
EXPECTED_SCHEDULES = {
    "daily-news-auto-short.yml": ["45 2 * * *", "45 13 * * *"],
    "daily-news-shorts.yml": ["30 2 * * *", "30 13 * * *"],
    "make_longform.yml": ["0 13 * * 0"],
    "make_shorts.yml": ["30 3 * * *"],
}
EXPECTED_SECRETS = {
    "CEREBRAS_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "COVERR_API_KEY",
    "DISCORD_WEBHOOK_URL", "DVIDS_API_KEY", "DVIDS_API_REFERER", "EUROPEANA_API_KEY",
    "GEMINI_API_KEY", "GITHUB_TOKEN", "GROQ_API_KEY", "KAGGLE_API_TOKEN", "KAGGLE_KEY",
    "KAGGLE_USERNAME", "MISTRAL_API_KEY", "NARA_API_KEY", "NASA_API_KEY",
    "OPENROUTER_API_KEY", "OPENVERSE_CLIENT_ID", "OPENVERSE_CLIENT_SECRET", "PIXABAY_API_KEY",
    "REPLICATE_API_TOKEN", "SLACK_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "YOUTUBE_API_KEY", "YOUTUBE_DATA_API_KEY", "YT_API_KEY", "YT_CLIENT_ID",
    "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN",
}
EXPECTED_VARIABLES = {
    "BUILD_RECOVERY_ATTEMPTS", "DVIDS_API_REFERER", "FOOTAGE_CONTACT",
    "GEMINI_FALLBACK_MODELS", "GEMINI_FALLBACK_MODELS_LONGFORM", "GEMINI_MODEL",
    "GEMINI_MODEL_LONGFORM", "KAGGLE_KERNEL_SLUG", "REPLICATE_VIDEO_MODEL",
    "YT_CHANNEL_HANDLE", "YT_CHANNEL_ID",
}
EXPECTED_TRIGGERS = {
    "daily-news-auto-short.yml": ["workflow_dispatch", "schedule"],
    "daily-news-shorts.yml": ["schedule", "workflow_dispatch"],
    "make_longform.yml": ["workflow_dispatch", "schedule", "pull_request"],
    "make_shorts.yml": ["workflow_dispatch", "schedule"],
    "release_documentary_auto.yml": ["workflow_run"],
    "release_documentary_private.yml": ["workflow_dispatch"],
    "release_shorts_private.yml": ["workflow_dispatch"],
    "manual-youtube-private-upload.yml": ["workflow_dispatch"],
}
UPLOAD_WORKFLOWS = {
    "daily-news-auto-short.yml", "make_shorts.yml",
    "release_documentary_auto.yml", "release_documentary_private.yml",
    "release_shorts_private.yml",
}


def _load_generator():
    spec = importlib.util.spec_from_file_location("protected_manifest_generator", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProtectedWorkflowManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = _load_manifest()
        cls.generator = _load_generator()

    def test_baseline_identity_and_schema(self):
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertEqual(self.manifest["baseline"]["commit"], EXPECTED_BASELINE)
        self.assertEqual(self.manifest["baseline"]["repository"], "ay23-byte/ai-studio-automation")
        self.assertEqual(self.manifest["baseline"]["channel_handle"], "@engineeringphysics-n5g")
        self.assertEqual(self.manifest["invariants"]["production_privacy_status"], "private")
        self.assertFalse(self.manifest["invariants"]["public_publish_allowed_in_protected_workflows"])
        self.assertFalse(self.manifest["invariants"]["automatic_public_release"])

    def test_manifest_matches_a_fresh_read_of_the_checkout(self):
        # The manifest must be reproducible; a stale hand-edited manifest is a failure.
        self.assertEqual(self.manifest, self.generator.build_manifest())

    def test_protected_workflow_file_set_and_hashes_are_unchanged(self):
        expected_paths = {str(p.relative_to(ROOT))
                          for p in (ROOT / ".github" / "workflows").glob("*.yml")}
        expected_paths |= {str(p.relative_to(ROOT))
                           for p in (ROOT / ".github").iterdir() if p.is_file()}
        self.assertEqual(set(self.manifest["immutable_files"]), expected_paths)
        for relative, digest in self.manifest["immutable_files"].items():
            self.assertTrue((ROOT / relative).is_file(), f"protected file disappeared: {relative}")
            self.assertEqual(_sha(ROOT / relative), digest, f"protected file changed: {relative}")

    def test_monitored_production_modules_are_unchanged(self):
        self.assertGreater(len(self.manifest["monitored_files"]), 100)
        for relative, digest in self.manifest["monitored_files"].items():
            self.assertTrue((ROOT / relative).is_file(), f"production module disappeared: {relative}")
            self.assertEqual(_sha(ROOT / relative), digest, f"production module changed: {relative}")

    def test_schedules_are_exactly_the_baseline_schedules(self):
        self.assertEqual(self.manifest["schedules"], EXPECTED_SCHEDULES)
        self.assertEqual(self.manifest["invariants"]["protected_workflow_count"], 35)

    def test_secret_and_variable_names_are_unchanged(self):
        self.assertEqual(set(self.manifest["secrets"]), EXPECTED_SECRETS)
        self.assertEqual(set(self.manifest["variables"]), EXPECTED_VARIABLES)
        self.assertEqual(self.manifest["secrets"], sorted(self.manifest["secrets"]))
        self.assertEqual(self.manifest["variables"], sorted(self.manifest["variables"]))

    def test_critical_workflow_triggers_are_unchanged(self):
        for name, triggers in EXPECTED_TRIGGERS.items():
            with self.subTest(workflow=name):
                self.assertEqual(self.manifest["workflows"][name]["triggers"], triggers)

    def test_dispatch_inputs_are_still_present(self):
        expected = {
            "make_shorts.yml": {"topic", "category", "channel_profile", "language", "upload", "force"},
            "make_longform.yml": {"topic", "category", "semantic_review", "minutes", "language",
                                  "channel_profile", "voice_gender", "presenter_mode", "visual_mode"},
            "daily-news-auto-short.yml": {"language"},
            "release_documentary_private.yml": {"source_run_id", "video_sha256", "review_decisions",
                                                "review_decisions_path", "confirm_private_upload"},
            "release_shorts_private.yml": {"mode", "source_run_id", "video_sha256", "review_decisions",
                                           "review_decisions_path", "confirm_private_upload"},
        }
        for name, input_names in expected.items():
            with self.subTest(workflow=name):
                self.assertEqual(set(self.manifest["workflows"][name]["dispatch_inputs"]), input_names)

    def test_all_upload_workflows_force_private_and_disable_public_release(self):
        for name in UPLOAD_WORKFLOWS:
            with self.subTest(workflow=name):
                lines = self.manifest["workflows"][name]["privacy_lines"]
                self.assertIn('PRIVACY: "private"', lines)
                self.assertIn('ALLOW_PUBLIC_PUBLISH: "0"', lines)

    def test_no_workflow_uses_a_public_privacy_literal_or_a_non_private_default(self):
        for name in self.manifest["workflows"]:
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertNotIn('privacyStatus: "public"', text)
                self.assertNotIn('privacy_status="public"', text)
                self.assertNotIn("PRIVACY: public", text)
                self.assertNotIn("PRIVACY: unlisted", text)

    def test_release_workflows_still_target_the_configured_channel(self):
        release_workflows = ["make_shorts.yml", "make_longform.yml", "daily-news-auto-short.yml",
                             "release_documentary_auto.yml", "release_documentary_private.yml",
                             "release_shorts_private.yml"]
        for name in release_workflows:
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("@engineeringphysics-n5g", text)
                self.assertIn("YT_REFRESH_TOKEN", text)

    def test_private_release_entrypoints_are_monitored(self):
        monitored = set(self.manifest["monitored_files"])
        for path in ("core/uploader.py", "core/publish.py", "core/release_gate.py",
                     "core/news_private_release.py", "scripts/upload_documentary_artifact.py",
                     "scripts/release_review_package.py", "scripts/run_daily_news_short.py",
                     "run.py", "run_daily.py"):
            self.assertIn(path, monitored)


if __name__ == "__main__":
    unittest.main(verbosity=2)
