import re
import threading
import time
from urllib.parse import urlsplit, urljoin


def validate_site(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('请输入学校 Canvas 的 HTTPS 地址，例如 https://canvas.example.edu')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('只填写 Canvas 站点地址，不包含课程路径、查询参数或 #')
    return value.strip().rstrip('/')


class CanvasSync:
    def __init__(self, store, data_dir):
        self.store, self.data_dir = store, data_dir
        self.lock = threading.Lock()
        self.confirm = threading.Event()
        self.cancel = threading.Event()
        self.status = {'phase': 'idle', 'message': '尚未开始同步'}

    def update(self, phase, message):
        with self.lock:
            self.status = {'phase': phase, 'message': message}

    def start(self):
        with self.lock:
            if self.status['phase'] in ('opening', 'login', 'syncing'):
                raise ValueError('同步已在进行中')
            site = validate_site(self.store.get('site', ''))
            self.confirm.clear()
            self.cancel.clear()
            self.status = {'phase': 'opening', 'message': '正在打开专用浏览器…'}
        threading.Thread(target=self.run, args=(site,), daemon=True).start()

    def run(self, site):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                with pw.chromium.launch_persistent_context(
                    str(self.data_dir / 'browser-profile'), headless=False,
                    viewport={'width': 1100, 'height': 800}
                ) as context:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(site, wait_until='domcontentloaded', timeout=60000)
                    self.update('login', '请在浏览器中完成登录和 MFA，然后点击“登录完成，开始读取”。')
                    deadline = time.monotonic() + 900
                    while not self.confirm.is_set():
                        if self.cancel.is_set():
                            raise ValueError('已取消同步')
                        if time.monotonic() > deadline:
                            raise ValueError('登录等待已超过 15 分钟，请重新开始同步')
                        page.wait_for_timeout(300)
                    page.goto(site, wait_until='domcontentloaded', timeout=60000)
                    if urlsplit(page.url).netloc != urlsplit(site).netloc:
                        raise ValueError('登录尚未完成，请完成学校登录后重新同步')
                    self.update('syncing', '正在验证登录并读取课程…')

                    def get(url):
                        if self.cancel.is_set():
                            raise ValueError('已取消同步')
                        if urlsplit(url).netloc != urlsplit(site).netloc or not url.startswith(site + '/api/'):
                            raise ValueError('Canvas 返回了非本站 API 地址，已停止读取')
                        result = page.evaluate('''async url => {
                            const r = await fetch(url, {credentials:'same-origin', redirect:'error',
                                headers:{Accept:'application/json'}, signal:AbortSignal.timeout(30000)});
                            return {ok:r.ok,status:r.status,type:r.headers.get('content-type'),
                                    link:r.headers.get('link'),body:await r.text()};
                        }''', url)
                        if not result['ok'] or 'json' not in (result['type'] or ''):
                            raise ValueError(f"Canvas 读取失败（HTTP {result['status']}）：登录可能过期，或学校不允许会话读取。缓存未变更。")
                        import json
                        return json.loads(result['body']), result['link'] or ''

                    def listing(path):
                        url, output, seen = site + path, [], set()
                        while url:
                            if url in seen or len(seen) >= 1000:
                                raise ValueError('分页异常，已停止同步')
                            seen.add(url)
                            payload, link = get(url)
                            if not isinstance(payload, list):
                                raise ValueError('Canvas 返回了意外的数据格式')
                            output.extend(payload)
                            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
                            url = urljoin(site, match[1]) if match else None
                        return output

                    me, _ = get(site + '/api/v1/users/self/profile')
                    courses = listing('/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=100')
                    tasks = []
                    for index, course in enumerate(courses):
                        cid = str(int(course['id']))
                        self.update('syncing', f"读取课程 {index+1}/{len(courses)}：{course.get('name', cid)}")
                        assignments = listing(f'/api/v1/courses/{cid}/assignments?per_page=100')
                        for a in assignments:
                            if a.get('published') is False:
                                continue
                            aid = str(int(a['id']))
                            tasks.append({'id': f'{cid}:{aid}', 'course': course.get('name', cid),
                                          'title': a['name'], 'due': a.get('due_at'),
                                          'url': f'{site}/courses/{cid}/assignments/{aid}'})
                    self.store.snapshot(tasks, site, me['id'])
                    self.update('done', f'同步完成：{len(courses)} 门课程，{len(tasks)} 项作业。浏览器已关闭。')
        except Exception as exc:
            message = str(exc)
            if 'Executable doesn' in message or 'No module named' in message:
                message = '浏览器依赖尚未安装，请先运行 setup.ps1。'
            # Never expose page bodies, cookies or token-bearing redirect URLs.
            elif not isinstance(exc, ValueError):
                message = f'浏览器操作失败（{type(exc).__name__}）。请确认网络、浏览器安装和登录状态后重试。'
            self.update('error', message)
