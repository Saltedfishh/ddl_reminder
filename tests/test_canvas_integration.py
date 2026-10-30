"""Browser-level sync test with a fully intercepted Canvas fixture (no school login)."""
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reminder.canvas import CanvasSync
from reminder.store import Store

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('playwright') and (ROOT / 'data' / 'browsers').exists(), 'Run setup.ps1 for browser integration test')
class CanvasIntegration(unittest.TestCase):
    def test_paginated_sync_then_failed_sync_preserves_cache(self):
        from playwright.sync_api import sync_playwright as real_playwright
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / 'data' / 'browsers')
        os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'work' / 'tmp')
        with tempfile.TemporaryDirectory(dir=ROOT / 'work') as temp:
            store = Store(Path(temp) / 'db.sqlite')
            sync = CanvasSync(store, Path(temp))
            failure = [False]
            requests = []

            def route_handler(route):
                url = route.request.url
                requests.append(url)
                headers = {'content-type': 'application/json'}
                if url.endswith('/api/v1/users/self/profile'):
                    data = {'id': 7}
                elif '/api/v1/courses?' in url:
                    data = [{'id': 1, 'name': 'Example course'}]
                elif '/assignments?' in url:
                    if 'page=2' in url:
                        if failure[0]:
                            route.fulfill(status=403, headers=headers, body='{}')
                            return
                        data = [{'id': 22, 'name': 'Undated', 'due_at': None}]
                    else:
                        data = [{'id': 21, 'name': 'Essay', 'due_at': '2026-12-01T23:59:00+10:30'}]
                        headers['link'] = '<https://canvas.test/api/v1/courses/1/assignments?page=2>; rel="next"'
                else:
                    route.fulfill(status=200, content_type='text/html', body='<h1>Canvas fixture</h1>')
                    return
                route.fulfill(status=200, headers=headers, body=json.dumps(data))

            @contextmanager
            def intercepted_playwright():
                with real_playwright() as pw:
                    def launch(*args, **kwargs):
                        kwargs['headless'] = True
                        context = pw.chromium.launch_persistent_context(*args, **kwargs)
                        context.route('**/*', route_handler)
                        return context
                    yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

            with patch('playwright.sync_api.sync_playwright', intercepted_playwright):
                sync.confirm.set()
                sync.run('https://canvas.test')
                self.assertEqual(sync.status['phase'], 'done', sync.status)
                self.assertEqual(len(store.tasks()), 2)
                self.assertTrue(any('page=2' in url for url in requests))
                last = store.get('last_sync')
                failure[0] = True
                sync.run('https://canvas.test')
                self.assertEqual(sync.status['phase'], 'error')
                self.assertEqual(store.get('last_sync'), last)
                self.assertEqual(len(store.tasks()), 2)
