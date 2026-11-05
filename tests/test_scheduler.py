import unittest
from datetime import datetime, timezone, timedelta
from core.scheduler import apply_planned_schedules


class SchedulerTests(unittest.TestCase):
    def test_only_approved_uploaded_items_are_scheduled(self):
        calls = []
        rows = [
            {"id": 1, "approved": 1, "youtube_id": "abc", "planned_publish_at": "2030-01-01T19:00:00+05:30"},
            {"id": 2, "approved": 0, "youtube_id": "def", "planned_publish_at": "2030-01-01T19:00:00+05:30"},
            {"id": 3, "approved": 1, "youtube_id": None, "planned_publish_at": "2030-01-01T19:00:00+05:30"},
        ]
        result = apply_planned_schedules(
            rows, lambda vid, when: calls.append((vid, when)),
            lambda *args, **kwargs: None,
            now=datetime(2029, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(len(calls), 1)
        self.assertEqual([x["status"] for x in result], ["scheduled"])

    def test_skips_too_soon_and_already_scheduled(self):
        now = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
        rows = [
            {"id": 1, "approved": 1, "youtube_id": "abc", "planned_publish_at": "2030-01-01T17:30:00+05:30"},
            {"id": 2, "approved": 1, "youtube_id": "def", "planned_publish_at": "2030-01-02T18:00:00+05:30", "scheduled_at": "2030-01-02T18:00:00+05:30"},
        ]
        result = apply_planned_schedules(rows, lambda *a: None, lambda *a, **k: None, now=now)
        self.assertEqual([x["status"] for x in result], ["too_soon", "already_scheduled"])


if __name__ == "__main__":
    unittest.main()
