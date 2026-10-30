import argparse
import json
import os
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from reminder.canvas import CanvasSync, validate_site
from reminder.notify import check_reminders, windows_toast
from reminder.store import Store

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / 'data' / 'browsers'))


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR allows multiple servers on one port, which can leave
    # browser requests hanging. Reserve this port exclusively instead.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    parser = argparse.ArgumentParser(description='Canvas 本地 DDL 提醒')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    store = Store(args.data_dir / 'reminder.db')
    sync = CanvasSync(store, args.data_dir)
    csrf = secrets.token_urlsafe(32)
    origin = f'http://127.0.0.1:{args.port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, status=200, html=False):
            content = value.encode('utf-8') if html else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def allowed_host(self):
            return self.headers.get('Host') == f'127.0.0.1:{args.port}'

        def do_GET(self):
            if not self.allowed_host():
                return self.respond({'error': 'Host 不允许'}, 403)
            if self.path == '/':
                return self.respond((ROOT / 'web' / 'index.html').read_text(encoding='utf-8').replace('__CSRF__', csrf), html=True)
            if self.path == '/api/health':
                return self.respond({'app': 'canvas-ddl-reminder', 'ok': True})
            if self.path == '/api/state':
                with sync.lock:
                    status = dict(sync.status)
                return self.respond({'site': store.get('site', ''), 'thresholds': store.get('thresholds', [72, 24, 3]),
                    'last_sync': store.get('last_sync'), 'tasks': store.tasks(), 'sync': status,
                    'notices': store.notices(), 'notification_error': store.get('notification_error')})
            self.respond({'error': '不存在'}, 404)

        def do_POST(self):
            if not self.allowed_host() or self.headers.get('Origin') != origin or self.headers.get('X-CSRF-Token') != csrf:
                return self.respond({'error': '请求来源验证失败，请刷新面板'}, 403)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 8192:
                    raise ValueError('请求大小不正确')
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/settings':
                    site = validate_site(data['site'])
                    owner = store.get('owner')
                    if owner and owner[0] != site:
                        raise ValueError('已有缓存绑定其他 Canvas 站点，请先备份并移走 data 目录后重启')
                    thresholds = data['thresholds']
                    if not isinstance(thresholds, list) or not 1 <= len(thresholds) <= 10 or any(type(h) not in (int, float) or not 0 < h <= 8760 for h in thresholds):
                        raise ValueError('提醒时间应为 0～8760 之间的正数（小时），最多 10 个')
                    store.set('site', site)
                    store.set('thresholds', sorted(set(thresholds), reverse=True))
                elif self.path == '/api/sync/start':
                    sync.start()
                elif self.path == '/api/sync/confirm':
                    if sync.status['phase'] != 'login':
                        raise ValueError('当前没有等待登录的同步')
                    sync.confirm.set()
                elif self.path == '/api/sync/cancel':
                    sync.cancel.set()
                elif self.path == '/api/complete':
                    if type(data['done']) is not bool:
                        raise ValueError('完成状态不正确')
                    store.complete(str(data['id']), data['done'])
                elif self.path == '/api/notify/test':
                    windows_toast('Canvas DDL 提醒', '测试通知：后台提醒已连接。')
                    store.set('notification_error', None)
                    store.notice('测试通知', '已向 Windows 发送测试通知，请确认是否显示。')
                else:
                    return self.respond({'error': '不存在'}, 404)
                self.respond({'ok': True})
            except (ValueError, KeyError, TypeError) as exc:
                self.respond({'error': str(exc)}, 400)
            except Exception as exc:
                self.respond({'error': str(exc)}, 500)

    # Bind before starting the scheduler: a second launch must not duplicate reminders.
    try:
        server = LocalServer(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        # A second manual launch should open the existing app, without duplicating reminders.
        import urllib.request
        try:
            with urllib.request.urlopen(origin + '/api/health', timeout=2) as response:
                healthy = json.load(response).get('app') == 'canvas-ddl-reminder'
        except Exception:
            healthy = False
        if healthy:
            print(f'面板已在运行：{origin}', flush=True)
            if not args.no_browser:
                webbrowser.open(origin)
            return
        raise RuntimeError(f'无法启动本地面板：端口 {args.port} 不可用。请关闭占用此端口的程序，或使用 --port 指定其他端口。') from exc
    stop = threading.Event()

    def scheduler():
        while not stop.is_set():
            try:
                check_reminders(store, time.time())
            except Exception as exc:
                store.set('notification_error', f'提醒检查失败：{type(exc).__name__}')
            stop.wait(30)

    threading.Thread(target=scheduler, daemon=True).start()
    print(f'Canvas DDL 面板：{origin}\n保持此程序运行以接收提醒。Ctrl+C 退出。', flush=True)
    if not args.no_browser:
        webbrowser.open(origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        sync.cancel.set()
        server.server_close()


if __name__ == '__main__':
    main()
