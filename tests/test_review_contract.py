"""Shared review contract: what blocks a release, and what cannot be overridden."""
import base64
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import review_contract as contract
from tests.review_package_fixture import NOTE, approve, fake_media, make_package, source_asset


class _Package(unittest.TestCase):
    mode = "shortform"

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "package"
        self.root.mkdir()

    def build(self, **kwargs):
        kwargs.setdefault("mode", self.mode)
        self.manifest = make_package(self.root, **kwargs)
        return self.manifest

    def evaluate(self, decisions="approve", **kwargs):
        if decisions == "approve":
            decisions = approve(self.root)
        try:
            mode = json.loads((self.root / "production_manifest.json").read_text()).get("mode", self.mode)
        except (OSError, ValueError):
            mode = self.mode
        with fake_media(mode if mode in contract.MODES else self.mode):
            return contract.evaluate_release(self.root, decisions, **kwargs)

    def assertBlocked(self, report, fragment):
        self.assertFalse(report["allowed"], "release must be blocked")
        joined = "\n".join(report["blocking"])
        self.assertIn(fragment, joined)

    def check(self, report, name):
        return next(item for item in report["checks"] if item["name"] == name)


class ApprovalTests(_Package):
    def test_complete_bound_decisions_allow_release_for_each_mode(self):
        for mode in contract.MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                manifest = self.build(mode=mode)
                report = self.evaluate(expected_sha256=manifest["video"]["sha256"], expected_mode=mode,
                                       expected_run_id="4242", expected_job_id=manifest["job_id"])
                self.assertTrue(report["allowed"], report["blocking"])
                self.assertEqual({item["status"] for item in report["checks"]}, {"PASS"})

    def test_a_new_package_is_review_required_and_never_approved(self):
        self.build()
        summary = json.loads((self.root / "package_summary.json").read_text())
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertFalse(summary["publication_eligible"])
        checklist = json.loads((self.root / "review_checklist.json").read_text())
        self.assertEqual(checklist["human_review_status"], "pending")
        for scene in self.manifest["scenes"]:
            self.assertEqual(scene["assets"][0]["rights"]["status"], "unreviewed")

    def test_no_decisions_means_nothing_is_approved(self):
        self.build()
        report = self.evaluate(decisions=None)
        self.assertBlocked(report, "No human review decisions were supplied")
        self.assertEqual(self.check(report, "visual review")["status"], "BLOCKED")
        self.assertEqual(self.check(report, "rights review")["status"], "BLOCKED")

    def test_untouched_template_approves_nothing(self):
        manifest = self.build()
        template = contract.decision_template(manifest)
        self.assertEqual(template["overall"]["decision"], "pending")
        self.assertEqual({item["decision"] for item in template["scene_reviews"]}, {"pending"})
        self.assertEqual({item["rights_decision"] for item in template["asset_reviews"]}, {"pending"})
        self.assertFalse(any(item["source_verified"] for item in template["asset_reviews"]))
        self.assertEqual(set(template["confirmations"].values()), {"pending"})
        report = self.evaluate(decisions=template)
        for name in ("editorial review", "visual review", "source review", "rights review"):
            self.assertEqual(self.check(report, name)["status"], "BLOCKED", name)

    def test_job_ids_differ_between_runs_even_with_the_same_database_id(self):
        first = contract.make_job_id("shortform", 1, {"GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1"})
        second = contract.make_job_id("shortform", 1, {"GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1"})
        retry = contract.make_job_id("shortform", 1, {"GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "2"})
        self.assertEqual(len({first, second, retry}), 3)


class BindingTests(_Package):
    def test_stale_approval_for_another_video_is_rejected(self):
        self.build()
        report = self.evaluate(approve(self.root, video_sha256="f" * 64))
        self.assertBlocked(report, "Stale approval: the decisions were made for a different video")

    def test_approval_for_another_job_is_rejected(self):
        self.build()
        report = self.evaluate(approve(self.root, job_id="shortform-run1-a1-j1"))
        self.assertBlocked(report, "different job")

    def test_approval_for_another_manifest_is_rejected(self):
        self.build()
        report = self.evaluate(approve(self.root, manifest_fingerprint="0" * 64))
        self.assertBlocked(report, "different manifest")

    def test_missing_binding_fields_are_not_inferred(self):
        self.build()
        for key in ("job_id", "mode", "video_sha256", "manifest_fingerprint"):
            with self.subTest(key=key):
                decisions = approve(self.root)
                del decisions[key]
                self.assertBlocked(self.evaluate(decisions), f"{key} is missing")

    def test_changed_video_invalidates_the_approval(self):
        self.build()
        decisions = approve(self.root)
        with open(self.root / "video.mp4", "ab") as handle:
            handle.write(b"re-rendered")
        report = self.evaluate(decisions)
        self.assertBlocked(report, "Changed video")

    def test_changed_asset_file_is_detected(self):
        self.build()
        decisions = approve(self.root)
        (self.root / "assets" / "scene_01.jpg").write_bytes(b"another image")
        self.assertBlocked(self.evaluate(decisions), "asset file changed after the package was built")

    def test_edited_manifest_breaks_the_fingerprint(self):
        self.build()
        decisions = approve(self.root)
        manifest = json.loads((self.root / "production_manifest.json").read_text())
        manifest["scenes"][0]["narration"] = "A different claim entirely."
        (self.root / "production_manifest.json").write_text(json.dumps(manifest))
        self.assertBlocked(self.evaluate(decisions), "manifest was edited")

    def test_release_request_must_match_hash_mode_job_and_run(self):
        manifest = self.build()
        self.assertBlocked(self.evaluate(expected_sha256="e" * 64), "does not match the hash given for this release")
        self.assertBlocked(self.evaluate(expected_mode="longform"), "Wrong production mode")
        self.assertBlocked(self.evaluate(expected_job_id="other-job"), "Wrong job")
        self.assertBlocked(self.evaluate(expected_run_id="999"), "Wrong artifact")
        self.assertTrue(self.evaluate(expected_sha256=manifest["video"]["sha256"].upper())["allowed"])

    def test_review_dated_before_the_build_is_stale(self):
        self.build()
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.assertBlocked(self.evaluate(approve(self.root, reviewed_at=old)), "dated before this package was built")

    def test_reviewer_and_timestamp_are_required(self):
        self.build()
        self.assertBlocked(self.evaluate(approve(self.root, reviewer="")), "named human reviewer")
        self.assertBlocked(self.evaluate(approve(self.root, reviewed_at="2026-11-13T12:00:00")), "with a timezone")
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertBlocked(self.evaluate(approve(self.root, reviewed_at=future)), "in the future")

    def test_decisions_for_unknown_scenes_or_assets_are_rejected(self):
        self.build()
        decisions = approve(self.root)
        decisions["scene_reviews"].append({"scene_id": "scene-99", "decision": "approved", "notes": NOTE})
        self.assertBlocked(self.evaluate(decisions), "scene 'scene-99', which is not in this package")

    def test_old_decision_schema_is_rejected(self):
        self.build()
        self.assertBlocked(self.evaluate(approve(self.root, schema_version=1)), "must use schema_version 2")

    def test_non_object_decisions_are_rejected(self):
        self.build()
        self.assertBlocked(self.evaluate(decisions=["approved"]), "not a JSON object")


class DecisionCompletenessTests(_Package):
    def test_every_shot_needs_its_own_decision_and_notes(self):
        self.build()
        decisions = approve(self.root)
        decisions["scene_reviews"][1]["decision"] = "pending"
        report = self.evaluate(decisions)
        self.assertBlocked(report, "scene-02: the shot decision is 'pending'")
        self.assertEqual(self.check(report, "rights review")["status"], "PASS")

        decisions = approve(self.root)
        decisions["scene_reviews"][0]["notes"] = "ok"
        self.assertBlocked(self.evaluate(decisions), "scene-01: approval needs inspection notes")

        decisions = approve(self.root)
        decisions["scene_reviews"] = decisions["scene_reviews"][:1]
        self.assertBlocked(self.evaluate(decisions), "scene-02: no shot decision was recorded")

    def test_a_rejected_shot_blocks_the_video(self):
        self.build()
        decisions = approve(self.root)
        decisions["scene_reviews"][0].update(decision="rejected", notes="Wrong rocket, this is not Apollo 11.")
        self.assertBlocked(self.evaluate(decisions), "scene-01: the shot was rejected")

    def test_overall_rejection_blocks_even_when_everything_else_is_approved(self):
        self.build()
        decisions = approve(self.root)
        decisions["overall"] = {"decision": "rejected", "notes": "Pacing is wrong in the second half."}
        self.assertBlocked(self.evaluate(decisions), "The reviewer rejected the video")

    def test_each_confirmation_is_independent(self):
        self.build()
        for name in contract.CONFIRMATIONS:
            with self.subTest(confirmation=name):
                decisions = approve(self.root)
                decisions["confirmations"][name] = "pending"
                self.assertBlocked(self.evaluate(decisions), f"Human {name} confirmation is 'pending'")
                del decisions["confirmations"][name]
                self.assertBlocked(self.evaluate(decisions), f"Human {name} confirmation is missing")

    def test_source_and_rights_are_separate_decisions(self):
        self.build()
        decisions = approve(self.root)
        decisions["asset_reviews"][0]["source_verified"] = "yes"   # only the boolean true counts
        report = self.evaluate(decisions)
        self.assertEqual(self.check(report, "source review")["status"], "BLOCKED")
        self.assertEqual(self.check(report, "rights review")["status"], "PASS")

        decisions = approve(self.root)
        decisions["asset_reviews"][0]["rights_decision"] = "pending"
        report = self.evaluate(decisions)
        self.assertEqual(self.check(report, "source review")["status"], "PASS")
        self.assertEqual(self.check(report, "rights review")["status"], "BLOCKED")

    def test_duplicate_decisions_are_rejected(self):
        self.build()
        decisions = approve(self.root)
        decisions["asset_reviews"].append(dict(decisions["asset_reviews"][0]))
        self.assertBlocked(self.evaluate(decisions), "has more than one decision")

    def test_claims_and_research_sources_need_decisions(self):
        research = {"sources": [{"source_id": "S1", "title": "Paper", "publisher": "Nature", "url": "https://nature.com/x"}],
                    "claims": [{"claim_id": "C1", "text": "Water boils at 100 C at sea level.", "evidence": "p.2",
                                "source_ids": ["S1"]}]}
        self.build(research=research)
        self.assertTrue(self.evaluate()["allowed"])
        decisions = approve(self.root)
        decisions["claim_reviews"][0]["decision"] = "pending"
        report = self.evaluate(decisions)
        self.assertBlocked(report, "claim C1: decision is 'pending'")
        decisions = approve(self.root)
        decisions["source_reviews"] = []
        self.assertBlocked(self.evaluate(decisions), "research source S1: no decision was recorded")


class RightsEvidenceTests(_Package):
    def test_human_approval_cannot_override_a_missing_licence(self):
        self.build(assets=[source_asset(rights={"license": None}), source_asset()])
        decisions = approve(self.root)
        decisions["asset_reviews"][0]["license"] = "CC BY 4.0"       # a reviewer typing a licence changes nothing
        report = self.evaluate(decisions)
        self.assertBlocked(report, "no licence was recorded for this source asset")
        self.assertEqual(self.check(report, "rights evidence")["status"], "BLOCKED")
        self.assertEqual(self.check(report, "rights review")["status"], "PASS")

    def test_unknown_licence_stays_blocked(self):
        for label in ("All rights reserved", "Unknown", "Editorial use only", "CC BY-NC 4.0", "CC BY-ND 4.0"):
            with self.subTest(licence=label), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                self.build(assets=[source_asset(rights={"license": label}), source_asset()])
                self.assertBlocked(self.evaluate(), "is unclear or not accepted")

    def test_unknown_origin_stays_blocked(self):
        self.build(assets=[source_asset(provenance={"origin": "unknown"}), source_asset()])
        self.assertBlocked(self.evaluate(), "origin is unknown")

    def test_missing_origin_is_recorded_as_unknown_not_guessed(self):
        asset = source_asset()
        del asset["provenance"]["origin"]
        manifest = self.build(assets=[asset, source_asset()])
        self.assertEqual(manifest["scenes"][0]["assets"][0]["provenance"]["origin"], "unknown")

    def test_search_results_page_is_not_a_source(self):
        for url in ("https://www.google.com/search?q=apollo", "https://commons.wikimedia.org/w/index.php?search=apollo",
                    "https://example.org/results?query=apollo"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                self.build(assets=[source_asset(provenance={"source_url": url}), source_asset()])
                self.assertBlocked(self.evaluate(), "search-results page")

    def test_missing_source_url_blocks(self):
        self.build(assets=[source_asset(provenance={"source_url": None}), source_asset()])
        self.assertBlocked(self.evaluate(), "no original source page URL was recorded")

    def test_ai_asset_needs_its_real_prompt(self):
        ai = {"asset_type": "image", "provenance": {"origin": "ai_generated", "provider": "Pollinations"}, "rights": {}}
        self.build(assets=[ai, source_asset()])
        self.assertBlocked(self.evaluate(), "generation prompt of this AI asset was not recorded")

    def test_ai_asset_and_project_graphic_with_evidence_pass(self):
        ai = {"asset_type": "image", "rights": {},
              "provenance": {"origin": "ai_generated", "provider": "Pollinations", "prompt": "A blue nebula"}}
        graphic = {"asset_type": "image", "rights": {"license": "Project-created original graphic"},
                   "provenance": {"origin": "project_graphic", "provider": "AI Studio local graphic renderer"}}
        self.build(assets=[ai, graphic])
        self.assertTrue(self.evaluate()["allowed"])

    def test_attribution_and_licence_url_are_required_for_source_assets(self):
        self.build(assets=[source_asset(rights={"attribution": None, "license_url": None}), source_asset()])
        report = self.evaluate()
        self.assertBlocked(report, "attribution text is missing")
        self.assertBlocked(report, "no licence or terms URL")
        self.assertEqual(self.check(report, "rights evidence")["status"], "PASS")
        decisions = approve(self.root)
        decisions["asset_reviews"][0].update(attribution="NASA / KSC", license_url="https://www.nasa.gov/media-guidelines")
        self.assertTrue(self.evaluate(decisions)["allowed"])

    def test_reviewer_cannot_contradict_the_recorded_licence(self):
        self.build()
        decisions = approve(self.root)
        decisions["asset_reviews"][0]["license"] = "CC0"
        self.assertBlocked(self.evaluate(decisions), "differs from the recorded licence")


class QaTests(_Package):
    def test_failed_qa_can_never_be_overridden(self):
        for name in contract.HUMAN_RESOLVABLE_QA:
            with self.subTest(report=name), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                self.build(qa={name: "FAIL"})
                decisions = approve(self.root)
                decisions["qa_reviews"][name] = {"decision": "accepted", "notes": NOTE}
                self.assertBlocked(self.evaluate(decisions), f"qa/{name}.json is FAIL")

    def test_technical_qa_must_pass(self):
        for status in ("FAIL", "REVIEW", None):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                self.build(qa={"technical": status})
                self.assertBlocked(self.evaluate(), "technical QA must be PASS")

    def test_missing_or_corrupt_qa_report_blocks(self):
        self.build()
        decisions = approve(self.root)
        (self.root / "qa" / "audio.json").unlink()
        self.assertBlocked(self.evaluate(decisions), "qa/audio.json is NOT_RUN")
        (self.root / "qa" / "audio.json").write_text("{not json")
        self.assertBlocked(self.evaluate(decisions), "qa/audio.json is INVALID")

    def test_review_status_needs_written_human_acceptance(self):
        self.build()
        decisions = approve(self.root)
        decisions["qa_reviews"]["semantic"] = {"decision": "accepted", "notes": "fine"}
        self.assertBlocked(self.evaluate(decisions), "qa/semantic.json is REVIEW")
        decisions["qa_reviews"]["semantic"] = {"decision": "pending", "notes": NOTE}
        self.assertBlocked(self.evaluate(decisions), "qa/semantic.json is REVIEW")

    def test_undecodable_video_blocks(self):
        self.build()
        decisions = approve(self.root)
        with fake_media("shortform", decoded=False):
            report = contract.evaluate_release(self.root, decisions)
        self.assertBlocked(report, "Decode errors")

    def test_wrong_profile_blocks(self):
        self.build()
        decisions = approve(self.root)
        for overrides, message in (({"width": 1920, "height": 1080}, "Wrong aspect ratio"),
                                   ({"width": 720, "height": 1280}, "below the shortform minimum"),
                                   ({"video_codec": "vp9"}, "H.264 is required"),
                                   ({"audio_codec": "opus"}, "AAC is required")):
            with self.subTest(message=message), fake_media("shortform", **overrides):
                self.assertBlocked(contract.evaluate_release(self.root, decisions), message)

    def test_empty_captions_block(self):
        self.build()
        decisions = approve(self.root)
        (self.root / "captions.ass").write_text("")
        self.assertBlocked(self.evaluate(decisions), "captions file is missing or empty")


class PackageIntegrityTests(_Package):
    def test_schema_v1_package_can_never_be_released(self):
        self.build()
        manifest = json.loads((self.root / "production_manifest.json").read_text())
        manifest["schema_version"] = 1
        (self.root / "production_manifest.json").write_text(json.dumps(manifest))
        self.assertBlocked(self.evaluate(decisions=approve(self.root, manifest)), "manifest schema 1")

    def test_unsupported_or_missing_manifest_blocks(self):
        self.build()
        (self.root / "production_manifest.json").write_text(json.dumps({"schema_version": 9}))
        self.assertBlocked(self.evaluate(decisions=None), "Unsupported manifest schema_version")
        (self.root / "production_manifest.json").unlink()
        self.assertBlocked(self.evaluate(decisions=None), "Missing manifest")

    def test_paths_cannot_leave_the_package(self):
        for bad in ("../outside.mp4", "/etc/passwd", ""):
            with self.subTest(path=bad), self.assertRaises(contract.ContractError):
                contract.inside(self.root, bad)

    def test_manifest_paths_are_relative(self):
        manifest = self.build()
        text = json.dumps(manifest)
        self.assertNotIn(str(self.root), text)
        self.assertEqual(manifest["video"]["file"], "video.mp4")

    def test_decisions_text_accepts_json_and_compressed_form(self):
        payload = {"schema_version": 2, "reviewer": "A"}
        self.assertEqual(contract.load_decisions_text(json.dumps(payload)), payload)
        packed = "gz64:" + base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
        self.assertEqual(contract.load_decisions_text(packed), payload)
        for bad in ("", "gz64:not-base64!!", "[1, 2]", "{broken"):
            with self.subTest(text=bad), self.assertRaises(contract.ContractError):
                contract.load_decisions_text(bad)

    def test_oversized_compressed_decisions_are_refused(self):
        bomb = "gz64:" + base64.b64encode(gzip.compress(b" " * (9 * 1024 * 1024))).decode()
        with self.assertRaisesRegex(contract.ContractError, "larger than 8 MB"):
            contract.load_decisions_text(bomb)

    def test_write_package_removes_leftover_decisions(self):
        manifest = self.build()
        contract.write_json(self.root / contract.DECISIONS_NAME, approve(self.root))
        with fake_media():
            summary = contract.write_package(self.root, manifest)
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertFalse((self.root / contract.DECISIONS_NAME).exists())


if __name__ == "__main__":
    unittest.main()
