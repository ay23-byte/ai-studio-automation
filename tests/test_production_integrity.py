import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import dedupe, jobs


class ProductionIntegrityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_dynamic_output_corrupted_without_size_change_is_regenerated(self):
        cp = jobs.Checkpoint(17, self.root, mirror_to_db=False)
        def make():
            (self.root / 'frame').write_bytes(b'good')
            return {'path': 'frame'}
        cp.run('visual_0', make, input_hash='scene')
        self.assertTrue(cp.is_done('visual_0', 'scene'))
        (self.root / 'frame').write_bytes(b'junk')
        self.assertFalse(cp.is_done('visual_0', 'scene'))
        cp.run('visual_0', make, input_hash='scene')
        self.assertEqual((self.root / 'frame').read_bytes(), b'good')

    def test_lock_contention_does_not_touch_running_job(self):
        from filelock import FileLock, Timeout
        run = jobs.exclusive_build(lambda *args: self.fail('ran a second renderer'))
        with FileLock(self.root / '.production.lock'), self.assertRaises(Timeout):
            run({}, 1, self.root)
        self.assertFalse((self.root / 'checkpoint.json').exists())

    def test_two_long_lived_ledger_instances_preserve_other_jobs(self):
        a, b = (dedupe.Ledger(self.root / 'ledger.json') for _ in range(2))
        with patch('core.db.ledger_put'):
            a.record('shorts', 'Topic A', job_id=4, youtube_id='confirmed', status='uploaded')
            b.record('longform', 'Topic B')
            b.record('shorts', 'Topic A', job_id=5, status='built')
        entries = json.loads(a.path.read_text())['entries']
        self.assertEqual(len(entries), 2)
        self.assertEqual((entries[0]['job_id'], entries[0]['youtube_id'], entries[0]['status']),
                         (4, 'confirmed', 'uploaded'))
