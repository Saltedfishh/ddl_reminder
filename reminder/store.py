import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('日期必须包含时区')
    return dt.timestamp()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, course TEXT NOT NULL, title TEXT NOT NULL,
                    url TEXT NOT NULL, due TEXT, done INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1, changed INTEGER NOT NULL DEFAULT 0,
                    previous_due TEXT, synced TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    task_id TEXT, due TEXT, threshold INTEGER, sent TEXT,
                    PRIMARY KEY(task_id, due, threshold));
                CREATE TABLE IF NOT EXISTS notices (
                    id INTEGER PRIMARY KEY, title TEXT, body TEXT, created TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def snapshot(self, tasks, site, user_id):
        """Commit a complete sync atomically; never erase cache on partial failure."""
        now = utcnow()
        # Validate every item before changing the database.
        for task in tasks:
            timestamp(task['due'])
        with self.db() as db:
            owner = db.execute("SELECT value FROM settings WHERE key='owner'").fetchone()
            identity = [site, str(user_id)]
            if owner and json.loads(owner[0]) != identity:
                raise ValueError('登录账号与本地缓存不一致。请使用原账号，或备份并移走 data 目录后重启。')
            db.execute("INSERT OR REPLACE INTO settings VALUES ('owner',?)", (json.dumps(identity),))
            db.execute('UPDATE tasks SET active=0')
            for task in tasks:
                old = db.execute('SELECT due FROM tasks WHERE id=?', (task['id'],)).fetchone()
                changed = old is not None and old['due'] != task['due']
                db.execute('''INSERT INTO tasks (id,course,title,url,due,synced) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET course=excluded.course,title=excluded.title,
                    url=excluded.url,due=excluded.due,synced=excluded.synced,active=1,
                    changed=CASE WHEN ? THEN 1 ELSE tasks.changed END,
                    previous_due=CASE WHEN ? THEN tasks.due ELSE tasks.previous_due END''',
                    (task['id'], task['course'], task['title'], task['url'], task['due'], now, changed, changed))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('last_sync',?)", (json.dumps(now),))

    def tasks(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM tasks WHERE active=1 ORDER BY due IS NULL,due,title')]

    def complete(self, task_id, done):
        with self.db() as db:
            result = db.execute('UPDATE tasks SET done=? WHERE id=?', (int(done), task_id))
            if not result.rowcount:
                raise ValueError('作业不存在')

    def notice(self, title, body):
        with self.db() as db:
            db.execute('INSERT INTO notices(title,body,created) VALUES (?,?,?)', (title, body, utcnow()))
            db.execute('DELETE FROM notices WHERE id NOT IN (SELECT id FROM notices ORDER BY id DESC LIMIT 100)')

    def notices(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM notices ORDER BY id DESC LIMIT 20')]

    def reminder_candidates(self, now):
        thresholds = sorted(set(self.get('thresholds', [72, 24, 3])), reverse=True)
        with self.db() as db:
            for task in self.tasks():
                if task['done'] or not task['due']:
                    continue
                left = timestamp(task['due']) - now
                # One most relevant catch-up notification, rather than three on resume.
                reached = [h for h in thresholds if left <= h * 3600]
                if not reached:
                    continue
                stage = 0 if left <= 0 else min(reached)
                sent = db.execute('SELECT 1 FROM deliveries WHERE task_id=? AND due=? AND threshold<=?',
                                  (task['id'], task['due'], stage)).fetchone()
                if not sent:
                    yield task, stage, left

    def delivered(self, task, stage):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?)',
                       (task['id'], task['due'], stage, utcnow()))
