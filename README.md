# Canvas DDL 提醒（第一版）

本地缓存作业截止时间，显示实时倒计时，并发送 Windows 桌面提醒。支持手动标记完成、搜索、无截止日期列表、DDL 变更标记、休眠后提醒补发。默认提前 72、24、3 小时提醒，到期后另提醒一次。默认端口 `8765`，只监听 `127.0.0.1`。

## 运行原理与维护

核心就是：**浏览器负责登录和读取，SQLite 保存作业，Python 后台负责提醒，网页负责展示倒计时。**

```text
Canvas 登录＋MFA（你操作）
          ↓
Playwright 控制的专用浏览器
          ↓
用当前登录会话读取 Canvas 作业 API
          ↓
本地 SQLite 缓存
          ├─ 网页面板：显示作业和倒计时
          ├─ 提醒后台：定期检查是否到提醒时间
          └─ 可选 MCP：供 AI 查询缓存
```

### 从启动到提醒

1. **启动程序**：`Launch.cmd` → `start.ps1` → 项目虚拟环境里的 Python → `app.py`。程序在 `127.0.0.1:8765` 启动本地 HTTP 服务，同时运行提醒检查线程。Windows 下独占监听端口；重复启动时，通过健康检查识别已有服务，避免启动第二个提醒后台。
2. **登录和同步**：点击同步后，Playwright 打开专用浏览器。你完成 MFA，再点“登录完成，开始读取”。程序通过浏览器中的 `fetch`，利用当前登录会话调用 Canvas API，读取当前用户、有效学生课程和各课作业，并遍历分页。**主要读取 API 返回的 JSON，不依赖网页上的按钮或文字位置。** 当前版本不使用 OAuth，也不自动完成 MFA。
3. **保存缓存**：所有课程完整读取成功后，才一次性写入 SQLite；中途失败保留旧缓存。作业通过“课程 ID＋作业 ID”识别，重复同步会更新原记录。截止日期变化时保留旧日期，手动完成状态跨同步保留。
4. **展示和提醒**：面板每 **5 秒**读取一次本地状态，每 **1 秒**重新计算倒计时；后台每 **30 秒**检查提醒。提醒记录按“作业＋截止时间＋提醒阶段”去重。Windows Toast 不可用时回退到托盘气泡。

### 文件职责

| 文件 | 负责什么 |
|---|---|
| `app.py` | 本地 HTTP 接口、启动服务、运行提醒线程 |
| `reminder/canvas.py` | 浏览器登录流程、Canvas API 请求和分页 |
| `reminder/store.py` | 数据库、缓存更新、完成状态、提醒去重 |
| `reminder/notify.py` | 提醒触发与 Windows 通知 |
| `web/index.html` | 面板样式、筛选、倒计时和按钮交互 |
| `mcp_server.py` | 可选的 MCP 查询入口，只读取缓存 |
| `setup.ps1` | 创建虚拟环境、安装依赖和下载 Chromium |
| `Launch.cmd` / `start.ps1` | 日常启动入口 |
| `background.pyw` / `startup.ps1` | 可选的隐藏后台和开机启动入口 |
| `tests/` | 缓存、提醒、时区及模拟 Canvas 同步测试 |

### 本地运行数据

运行数据都在 `data/`：

- `reminder.db`：作业、设置、完成状态和提醒记录。
- `browser-profile/`：浏览器登录会话，**不要分享或提交到 Git**。
- `browsers/`：Playwright 使用的 Chromium 及配套运行文件。
- `background.log`：通过 `background.pyw` 启动时的后台日志。

`.gitignore` 已排除 `data/`、`.venv/`、`work/` 和 Python 缓存，因此这些本地运行内容不应随源码上传。

### 维护与排查

**倒计时实时变化，但 Canvas 数据只有手动同步后才更新；后台程序必须运行，通知才会触发。** MCP 只是额外的查询入口，不承担定时提醒。修改 Python 后端后需重启程序，修改面板文件后刷新页面。

| 现象 | 优先检查 |
|---|---|
| 同步失败 | `reminder/canvas.py`、学校网络、浏览器登录状态和 API 访问权限 |
| 提醒异常 | `reminder/notify.py`、后台是否运行、作业是否标记完成、Windows 通知及勿扰设置 |
| 缓存或完成状态异常 | `reminder/store.py`、数据库及最近同步时间 |
| 页面显示异常 | `web/index.html` 和浏览器控制台 |
| 本地页面连接超时 | 先检查 `Launch.cmd` 启动窗口是否仍在运行，再检查端口或窗口中的错误 |

修改核心逻辑后，可运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试和真实账号验收的区别见文末“验证”。

## 安装和启动

需要 Windows、Python 3.11 或更高版本，以及安装依赖时可访问互联网。

在本目录打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

安装使用项目内 `.venv`，不会修改全局 Python 软件包。Chromium 下载到 `data/browsers`。

启动后打开 <http://127.0.0.1:8765>：

1. 填学校 Canvas 站点根地址（如 `https://canvas.example.edu`），保存设置。
2. 点“登录并同步”，在专用浏览器窗口中亲自完成 SSO 和 MFA。
3. 回面板点“登录完成，开始读取”。成功后浏览器关闭，缓存写入本地。
4. 点“测试通知”，检查 Windows 通知中心。Windows 可能将通知显示为 Windows PowerShell；检查该应用的通知权限及勿扰设置。
5. 每天或老师修改安排后重新同步；程序不自动登录或刷新学校页面。

安装完成后，日常使用可直接在资源管理器中双击 **`Launch.cmd`**。它会从你的 Windows 桌面会话启动程序，并自动打开浏览器。保留启动窗口，关闭窗口会停止服务与提醒。如果浏览器显示 `127.0.0.1` 连接超时，先检查启动窗口是否仍在运行、有无报错；仅打开网址不会启动后台。

开发代理中的临时进程可能无法供桌面浏览器持续访问，因此第一次本机验收请使用上述双击入口启动。不要修改防火墙、关闭 MFA 或将服务绑定到公网地址来解决本地连接问题。

关闭面板不影响后台。关闭启动程序的终端会停止后台和提醒。倒计时按本机时钟、时区显示；请保持系统时间准确。

## 开机启动（可选）

以下命令在**你主动执行时**才向当前用户的启动文件夹写入快捷方式；安装步骤不会自动启用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1 -Action Install
```

下次登录 Windows 后隐藏运行后台，不自动打开面板。直接访问上述本地地址即可。重复启动不会运行第二个提醒后台。日志为 `data/background.log`。

取消开机启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1 -Action Remove
```

移除快捷方式不会结束当前后台；可在任务管理器中根据命令行识别本项目的 `pythonw.exe` 后结束。前台运行时 Ctrl+C 退出。

## 同步边界

- 目前通过专用浏览器中的已登录会话，只读请求当前用户资料、有效学生课程及各课作业列表，遍历分页。没有 OAuth 接入，也不要求你提供密码、令牌或 MFA 验证码。
- 会话读取能否使用需要在学校站点实测。学校若禁止，程序会报告失败，不自动绕过限制，也不会改动旧缓存；届时需要学校批准的 OAuth 或按实际网页开发适配器。
- 只处理作业列表中返回的截止时间，包括以作业形式出现的测验；不扫描公告、PDF、New Quizzes 详情或外部考试平台。
- 使用当前登录用户返回的 `due_at`。首次使用应拿一门有个人延期/小组截止时间的课程与 Canvas 页面核对；学校的定制功能仍需实测。
- 不根据分值推断课程总评权重；不启动考试、不提交作业、不修改 Canvas。
- 所有课程完整读取后才提交快照。任一课程出错则本次同步失败；成功快照中消失的作业退出活动列表，历史记录保留。
- 缓存绑定站点和账号，避免不同账户作业混在一起。要切换账号，先停止程序并备份、移走 `data` 目录，再重启。
- 本地手动“完成”状态会跨同步保留，不读取 Canvas 提交状态。

## 提醒规则

后台每 30 秒检查。首次同步时，已进入提醒窗口的作业立即进入最近阶段；过去的作业会补发一次到期通知，可手动标记完成。休眠恢复后只补发最近阶段，不把错过的多个阶段连发。同一作业、同一 DDL、同一阶段不重复；DDL 修改后按新时间计算。清除数据库会清除去重记录。

桌面通知交给 Windows 处理，专注模式可能隐藏弹窗。所有触发均保存在面板“提醒记录”（保留最近 100 条，展示 20 条）；通知调用失败会显示错误，避免每 30 秒重试刷屏，下一提醒阶段仍会尝试。电脑关机、用户未登录、后台未运行时无法发送提醒。

通知首先使用 Windows Toast；运行环境没有 PowerShell 应用标识时，回退到托盘气泡通知（不修改注册表）。气泡可能只短暂显示，不保证保留在系统通知中心，面板提醒记录始终保留。

## 数据位置

`data/reminder.db` 保存站点、作业、完成状态和提醒记录；`data/browser-profile` 保存专用登录会话。浏览器配置含敏感登录状态，**程序没有为整个配置目录额外加密**，请依赖 Windows 账户保护和磁盘权限，不要上传、分享或提交 `data`。项目忽略规则已排除该目录。应用不上传数据到 AI 或第三方服务。

## 可选 MCP 入口

`mcp_server.py` 使用官方 Python SDK 的 v1 API，提供 `list_deadlines` 和 `cache_status` 两个只读工具，只查询本地缓存，不负责登录或运行提醒后台。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

在支持本地 stdio MCP 的客户端中配置启动命令为本项目 `.venv\Scripts\python.exe` 的绝对路径，参数为 `mcp_server.py` 的绝对路径。通用配置示例（实际字段以客户端为准）：

```json
{"mcpServers":{"canvas-ddl":{"command":"D:\\PersonalProjects\\ddl_reminder\\.venv\\Scripts\\python.exe","args":["D:\\PersonalProjects\\ddl_reminder\\mcp_server.py"]}}}
```

接入 AI 客户端后，被查询的作业字段可能发送给其模型服务；浏览器会话和登录凭据不会通过这些工具返回。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖缓存事务、账号隔离、完成状态、DDL 修改、提醒去重、休眠补发、通知失败及日期时区。浏览器集成测试拦截所有网络请求，以模拟 Canvas 验证分页和中途读取失败时的缓存保护；没有真实学校账号验证，须由你登录后验收。

技术依据：[Canvas 作业 API](https://developerdocs.instructure.com/services/canvas/resources/assignments)、[Canvas 分页](https://developerdocs.instructure.com/get_started)。
