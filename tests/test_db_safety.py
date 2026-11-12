"""Database safety: concurrent writers, backup/restore, corruption, Git hygiene."""
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db

ROOT = Path(__file__).resolve().parents[1]


class _TempDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "studio.sqlite"
        redirect = patch.object(db, "DB_PATH", self.path)
        redirect.start()
        self.addCleanup(redirect.stop)
        db.init_db()


class ConcurrentWriterTests(_TempDatabase):
    def test_parallel_writers_lose_no_rows_and_never_see_a_locked_database(self):
        errors, ids = [], []
        guard = threading.Lock()

        def worker(index):
            try:
                for item in range(15):
                    new_id = db.save_video(f"job {index}-{item}", "shorts", "english", "physics", {"n": item})
                    db.update_video(new_id, status="done")
                    with guard:
                        ids.append(new_id)
            except Exception as exc:  # collected so the assertion shows the cause
                with guard:
                    errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 90)
        self.assertEqual(len(set(ids)), 90, "every writer must get its own row id")
        rows = db.list_videos(limit=500)
        self.assertEqual(len(rows), 90)
        self.assertTrue(all(row["status"] == "done" for row in rows))
        self.assertEqual(db.integrity_check(), "ok")

    def test_failed_transaction_is_rolled_back_completely(self):
        before = len(db.list_videos(limit=500))
        with self.assertRaises(RuntimeError):
            with db.transaction() as conn:
                conn.execute("INSERT INTO videos (title, format, status) VALUES ('half written', 'shorts', 'draft')")
                raise RuntimeError("crash in the middle of a job")
        self.assertEqual(len(db.list_videos(limit=500)), before)

    def test_duplicate_ledger_key_is_stored_once_under_concurrency(self):
        results, guard = [], threading.Lock()

        def claim(index):
            try:
                db.ledger_put("upload", "same-topic", title=f"writer {index}", job_id=index)
                outcome = "ok"
            except Exception as exc:
                outcome = type(exc).__name__
            with guard:
                results.append(outcome)

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(len(results), 8)
        matching = [row for row in db.ledger_list() if row["dedupe_key"] == "same-topic"]
        self.assertEqual(len(matching), 1, "one topic must never occupy two ledger rows")
        self.assertIsNotNone(db.ledger_get("upload", "same-topic"))


class BackupAndRecoveryTests(_TempDatabase):
    def test_backup_is_a_complete_restorable_copy(self):
        first = db.save_video("Keep me", "longform", "english", "physics", {"a": 1})
        db.update_video(first, youtube_id="abc123", status="uploaded")
        backup = db.backup_database("unit-test")
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())
        self.assertNotEqual(backup, self.path)

        # Disaster: the live file is destroyed after the backup was taken.
        db.save_video("Written after the backup", "shorts", "english", "physics", {})
        self.path.write_bytes(b"this is not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            db.integrity_check()

        # Recovery is a plain file copy of the newest backup.
        self.path.write_bytes(backup.read_bytes())
        self.assertEqual(db.integrity_check(), "ok")
        rows = db.list_videos()
        self.assertEqual([row["title"] for row in rows], ["Keep me"])
        self.assertEqual(rows[0]["youtube_id"], "abc123")

    def test_backups_never_overwrite_each_other_and_old_ones_are_pruned(self):
        db.save_video("row", "shorts", "english", "physics", {})
        with patch.dict("os.environ", {"AI_STUDIO_DB_BACKUPS_KEEP": "3"}):
            created = [db.backup_database(f"n{i}") for i in range(6)]
        self.assertEqual(len({str(path) for path in created}), 6)
        remaining = sorted((self.path.parent / "backups").glob("*.sqlite"))
        self.assertEqual(len(remaining), 3)
        self.assertEqual(remaining[-1], created[-1], "the newest backup is always kept")
        self.assertTrue(self.path.is_file(), "pruning must never touch the live database")

    def test_backup_of_a_missing_database_is_a_no_op(self):
        with patch.object(db, "DB_PATH", Path(self.tmp.name) / "absent.sqlite"):
            self.assertIsNone(db.backup_database("nothing"))

    def test_reinitialising_an_up_to_date_database_changes_nothing(self):
        db.save_video("row", "shorts", "english", "physics", {})
        before = [dict(row) for row in db.list_videos()]
        db.init_db()
        db.init_db()
        self.assertEqual([dict(row) for row in db.list_videos()], before)
        self.assertEqual(list((self.path.parent / "backups").glob("*.sqlite")) if (self.path.parent / "backups").exists() else [], [])


class GitHygieneTests(unittest.TestCase):
    def test_live_database_side_files_and_backups_are_ignored(self):
        for candidate in ("db/jobs.sqlite", "db/studio.sqlite-wal", "db/studio.sqlite-shm",
                          "db/backups/studio-20261112.sqlite", "state/publish_ledger.json.lock"):
            with self.subTest(path=candidate):
                result = subprocess.run(["git", "check-ignore", "-q", candidate], cwd=ROOT)
                self.assertEqual(result.returncode, 0, f"{candidate} must be ignored by Git")

    def test_no_workflow_stages_or_commits_a_database_or_media_file(self):
        offenders = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "git add" in stripped or "git commit" in stripped:
                    if any(token in stripped for token in (".sqlite", ".db", ".mp4", ".wav", " -A", " --all", " .")):
                        offenders.append(f"{path.name}:{number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_state_branch_helper_persists_json_only(self):
        source = (ROOT / "scripts" / "persist_state.py").read_text(encoding="utf-8")
        self.assertNotIn(".sqlite", source)
        self.assertIn("automation-state", source)


if __name__ == "__main__":
    unittest.main()
