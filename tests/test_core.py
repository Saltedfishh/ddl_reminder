import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from reminder.canvas import validate_site
from reminder.notify import check_reminders
from reminder.store import Store, timestamp


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'test.db')
        self.now = datetime(2026, 10, 30, tzinfo=timezone.utc)

    def task(self, hours=2, id='1:1'):
        return {'id': id, 'course': 'Course', 'title': 'Essay', 'url': 'https://example.edu/a',
                'due': (self.now + timedelta(hours=hours)).isoformat()}

    def sync(self, tasks):
        self.store.snapshot(tasks, 'https://example.edu', 12)

    def test_sync_updates_preserve_completion_and_detect_deadline_changes(self):
        a = self.task()
        self.sync([a])
        self.store.complete(a['id'], True)
        b = self.task(5)
        self.sync([b])
        task = self.store.tasks()[0]
        self.assertEqual(task['done'], 1)
        self.assertEqual(task['previous_due'], a['due'])
        self.assertEqual(task['changed'], 1)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_bad_sync_and_wrong_user_do_not_destroy_cache(self):
        self.sync([self.task()])
        invalid = self.task()
        invalid['due'] = 'invalid'
        with self.assertRaises(ValueError):
            self.sync([invalid])
        with self.assertRaises(ValueError):
            self.store.snapshot([], 'https://example.edu', 99)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_new_snapshot_deactivates_removed_tasks(self):
        self.sync([self.task(), self.task(id='1:2')])
        self.sync([self.task()])
        self.assertEqual(len(self.store.tasks()), 1)

    def test_reminder_stages_dedup_resume_and_due_change(self):
        self.sync([self.task(80)])
        calls = []
        def advance(hours):
            check_reminders(self.store, (self.now + timedelta(hours=hours)).timestamp(), lambda *a: calls.append(a))
        advance(0)
        self.assertEqual(len(calls), 0)
        advance(10)
        advance(10)
        self.assertEqual(len(calls), 1)
        advance(79)  # Skip 24-hour stage after sleep; only most relevant reminder.
        self.assertEqual(len(calls), 2)
        advance(81)
        advance(90)
        self.assertEqual(len(calls), 3)
        self.sync([self.task(100)])
        advance(99)
        self.assertEqual(len(calls), 4)

    def test_completed_and_undated_tasks_do_not_notify(self):
        a = self.task()
        b = self.task(id='1:2')
        b['due'] = None
        self.sync([a, b])
        self.store.complete(a['id'], True)
        self.assertEqual(list(self.store.reminder_candidates(self.now.timestamp())), [])

    def test_notification_failure_leaves_inbox_and_error_without_spam(self):
        self.sync([self.task()])
        def fail(*args):
            raise RuntimeError('test failure')
        check_reminders(self.store, self.now.timestamp(), fail)
        check_reminders(self.store, self.now.timestamp(), fail)
        self.assertEqual(len(self.store.notices()), 1)
        self.assertEqual(self.store.get('notification_error'), 'test failure')

    def test_timezone_and_url_validation(self):
        self.assertEqual(timestamp('2026-10-30T10:30:00+10:30'), timestamp('2026-10-30T00:00:00Z'))
        self.assertEqual(validate_site('https://example.edu/'), 'https://example.edu')
        for url in ['http://example.edu', 'https://user:pass@example.edu', 'https://example.edu/courses/1']:
            with self.assertRaises(ValueError):
                validate_site(url)


if __name__ == '__main__':
    unittest.main()
