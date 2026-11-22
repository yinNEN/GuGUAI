# Windows Server 部署指南

[English](windows-server-deploy.en.md) | 简体中文

本文档说明如何把本项目部署到 Windows Server 2019 / 2022（x64），并完成登录鉴权、API Key 托管、消息落库、文件缓冲与管理后台的初始化。

> 如果你的服务器在 NAT 内网、用 `http://公网IP:8000` 直接访问、没有域名和 HTTPS，请同时阅读 **[windows-server-nat-http.md](windows-server-nat-http.md)**。

---

## 0. 这个版本做了什么

上游项目 [`deepseek-ai/chat-demo`](https://github.com/deepseek-ai/chat-demo) 是一个**纯静态**聊天前端：API Key 保存在浏览器 `localStorage`，浏览器直接请求 `https://api.deepseek.com`，会话也只存在浏览器本地。

本仓库直接使用其**前端源码**（`chat_demo_score/chat-demo`）改造，并新增服务端。原有的流式对话、深度思考、联网搜索、文件/图片、语音、消息分支、历史导入导出全部保留。

| 能力 | 实现方式 |
| --- | --- |
| 统一登录 | `/login` 登录页，服务端签发 HttpOnly 会话 Cookie（SQLite 存储，可随时吊销） |
| 后端鉴权 | 所有页面与接口都在中间件中校验会话；管理员接口额外校验角色 |
| API Key 托管 | Key 加密后保存在服务器 SQLite；聊天、文件、语音请求全部由服务端带上 Key 转发，浏览器不再接触真实 Key |
| 会话同步 | 前端通过 `/api/sync/*` 上传/拉取完整会话快照（含分支、重命名、附件元数据），换设备登录即可看到全部会话 |
| 消息统一存储 | 发送前先推送会话快照，服务端再调用 DeepSeek；助手回复在流式结束后写入快照并投影到后台会话/消息表 |
| 文件缓冲 | 浏览器先把文件上传到服务器（所有文件单文件统一 32MB，默认 2GB 缓冲，超出按最旧文件删除），聊天时由服务器补传到 DeepSeek |
| 文本预览 | txt/cpp/py/js/json 等文本文件可在聊天框和消息内直接预览；服务端独立文本区存储（默认 1GB） |
| 存储管理 | 后台管理数据库/缓冲区/文本区上限、文件列表、会话清理与 VACUUM |
| 用户管理 | 后台创建/编辑/停用/删除用户、重置密码、设置管理员 |
| 审计 | 记录登录、用户管理、API Key 变更等操作 |
| 用户侧历史 | `/ui/history` 也可直接查看服务器端保存的本人全部会话 |
| 账号页面 | `/ui/account` 查看账号信息、修改密码；聊天界面的账号入口统一在设置中 |
| 暗黑模式 | 登录页、管理后台、用户页右上角可切换明暗模式，默认跟随系统 |

目录结构：

```
deepseek_local/
├─ chat_demo_score/chat-demo/ # 前端源码（React + TypeScript，已改造）
│  ├─ src/services/chat/sync.ts        # 会话同步
│  ├─ src/services/serverSession.ts    # 当前用户与登录跳转
│  └─ rspack.config.ts                 # API 同源、服务端托管开关、开发代理
├─ web/                       # 前端构建产物（由 chat-demo/dist 复制，后端实际提供）
│  ├─ index.html
│  └─ static/
├─ server/                    # 新增后端（FastAPI）
│  ├─ app/                    #   路由、鉴权、代理、同步、存储
│  ├─ pages/                  #   登录页 / 管理后台 / 会话记录页
│  ├─ run.py                  #   启动入口
│  ├─ install.ps1             #   一键安装依赖
│  ├─ start.bat               #   前台启动
│  ├─ install-service-nssm.ps1
│  ├─ install-startup-task.ps1
│  ├─ requirements.txt
│  ├─ .env.example            #   复制为 .env 后修改
│  └─ data/                   #   运行后生成：chat.db、secret.key、files/、text/、日志
├─ tests/                     # 联调用的 Mock 上游 + 端到端测试
└─ docs/windows-server-deploy.md
```

---

## 1. 部署架构

```
浏览器 ──HTTPS──> Caddy / IIS / Nginx（可选，负责证书）
                        │
                        ▼
              uvicorn 127.0.0.1:8000
                        │  携带服务端 API Key
                        ▼
              https://api.deepseek.com
```

- 生产环境建议：反向代理监听 443，应用只监听 `127.0.0.1:8000`。
- 所有业务数据在 `server/data/chat.db`（SQLite，WAL 模式）。
- 单进程运行即可（会话、限流都在进程内）；**不要**用 `--workers` 多进程，否则 SQLite 写入和限流会出现不一致。

---

## 2. 环境要求

- Windows Server 2019 / 2022，x64
- Python **3.12**（3.10+ 均可，推荐 3.12）
- 能出站访问 `https://api.deepseek.com`（HTTPS 443）
- 管理员权限（安装服务、放行防火墙）
- 可选：域名 + 公网/内网 DNS（用于 HTTPS）
- 可选：Node.js 22.12+ 与 pnpm（只有重新构建前端时需要）

---

## 3. 安装 Python

1. 下载 Windows x64 安装包：<https://www.python.org/downloads/windows/>（选择 Python 3.12 的 *Windows installer (64-bit)*）。
2. 安装时勾选 **Add python.exe to PATH**，选择 *Install for all users*。
3. 打开新的 PowerShell 验证：

```powershell
python --version
# Python 3.12.x
```

---

## 4. 复制项目

把整个项目目录复制到服务器，例如：

```
D:\apps\deepseek-chat\
├─ chat_demo_score\chat-demo\   # 前端源码（只在需要重新构建时用）
├─ web\                          # 已构建的前端产物（后端提供）
├─ server\
├─ tests\
└─ docs\
```

> 注意：`web/` 和 `server/` 必须保持同级目录；后端默认从 `..\web` 读取前端文件（可用 `WEB_DIR` 覆盖）。

### 4.1 重新构建前端（只有修改源码时才需要）

仓库里的 `web/` 已经是构建好的产物，直接部署即可。如果修改了 `chat_demo_score/chat-demo` 下的源码，需要重新构建：

1. 构建机安装 **Node.js 22.12+** 与 **pnpm**（`npm install -g pnpm`）；
2. 执行：

```powershell
cd D:\apps\deepseek-chat\chat_demo_score\chat-demo
pnpm install
pnpm build
Remove-Item ..\..\web\* -Recurse -Force
Copy-Item .\dist\* ..\..\web -Recurse -Force
```

也可以构建到其他目录，并通过 `WEB_DIR` 指过去：

```ini
WEB_DIR=D:\apps\deepseek-chat\chat_demo_score\chat-demo\dist
```

> 构建产物文件名带 hash，替换 `web/` 后建议重启服务并强制刷新浏览器（Ctrl+F5）。

---

## 5. 安装依赖并配置

以**管理员身份**打开 PowerShell：

```powershell
cd D:\apps\deepseek-chat\server
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

脚本会：

1. 检查 Python 版本；
2. 创建 `server\.venv` 虚拟环境；
3. 安装 `requirements.txt`；
4. 若不存在则从 `.env.example` 复制出 `.env`；
5. 创建 `server\data`。

如果服务器访问 PyPI 较慢，可先设置国内镜像再执行：

```powershell
python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

然后编辑配置：

```powershell
notepad D:\apps\deepseek-chat\server\.env
```

**必须确认**的项：

```ini
ADMIN_USERNAME=admin
# 建议设置强密码（至少 12 位，含大小写、数字、符号）；留空则自动生成随机密码
ADMIN_PASSWORD=请换成强密码
HOST=0.0.0.0
PORT=8000
COOKIE_SECURE=false          # 走 HTTPS 后改成 true
```

其余项保持默认即可。`ADMIN_PASSWORD` 只在数据库里没有任何用户时用于创建首个管理员。

---

## 6. 首次启动与初始化

### 6.1 启动

双击 `server\start.bat`，或在 PowerShell 中：

```powershell
cd D:\apps\deepseek-chat\server
.\.venv\Scripts\python.exe run.py
```

看到 `Uvicorn running on http://0.0.0.0:8000` 即启动成功。

- 若 `.env` 里配置了 `ADMIN_PASSWORD`：用它登录。
- 若留空：随机密码会写入 `server\data\initial-admin-password.txt`，同时打印在控制台和 `data\server.log` 中。

浏览器打开：

```
http://服务器IP:8000/login
```

### 6.2 后台初始化（管理员）

登录后默认进入 `/admin`：

1. **API Key** 页：粘贴 DeepSeek API Key（`sk-...`）→ **保存** → **测试连接**。
   - Key 会加密存进 `chat.db`，页面只显示脱敏值（如 `sk-abc****wxyz`）。
   - 也可用环境变量 `DEEPSEEK_API_KEY` 提供，但后台保存的值优先。
2. **用户管理**：新建普通用户（设置初始密码），或调整已有账号。
3. **概览**：确认“API Key 已配置”、用户/会话统计正常。
4. 点击左下角 **返回聊天**，即可开始对话。
5. 普通用户登录后直接进入聊天页；`/admin` 会被重定向，管理接口返回 403。

> 原版前端设置里仍可能显示“API Key”编辑框，这是前端自带 UI。普通用户输入的 Key 不会被服务端采用，也不会外泄；服务端始终使用后台托管的 Key。

### 6.3 验证清单

| 检查项 | 期望结果 |
| --- | --- |
| `http://IP:8000/healthz` | `{"ok":true,...}` |
| 未登录访问 `/` | 302 跳转 `/login` |
| 正确账号登录 | 进入聊天页，左下角显示用户名 |
| 发送一条消息 | 流式输出；侧边栏出现会话；刷新 `/ui/history` 能看到该会话 |
| 换一个浏览器/设备登录同一账号 | 能看到刚才的会话与消息 |
| 重命名/删除会话 | 刷新后保持一致 |
| 普通用户访问 `/admin` | 302 跳转 `/ui/history` |
| 普通用户访问 `/api/admin/apikey` | 403 |
| 管理员 API Key 页 | 只显示脱敏 Key |
| 关闭浏览器重开 | 会话仍有效（默认 7 天） |

---

## 7. 注册为 Windows 服务（开机自启）

### 7.1 方式 A：NSSM（推荐）

1. 下载 NSSM：<https://nssm.cc/download>，解压出 `win64\nssm.exe`，放到 `C:\tools\nssm\nssm.exe`。
2. 以管理员身份执行：

```powershell
cd D:\apps\deepseek-chat\server
powershell -ExecutionPolicy Bypass -File .\install-service-nssm.ps1 -NssmPath C:\tools\nssm\nssm.exe
```

3. 常用命令：

```powershell
Get-Service DeepSeekChat
Restart-Service DeepSeekChat
Stop-Service DeepSeekChat
Start-Service DeepSeekChat
```

日志：`server\data\service-out.log`、`server\data\service-err.log`、`server\data\server.log`。

### 7.2 方式 B：计划任务（无需第三方工具）

```powershell
cd D:\apps\deepseek-chat\server
powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1
```

任务以 `SYSTEM` 身份在开机时运行 `.venv\Scripts\python.exe run.py`（Session 0，无可见窗口），日志写入 `data\server.log`。

- 查看：`Get-ScheduledTask -TaskName DeepSeekChat`
- 删除：`Unregister-ScheduledTask -TaskName DeepSeekChat -Confirm:$false`

> 两种方式二选一，不要同时启用，否则会争抢 8000 端口。

### 7.3 防火墙

如果客户端直接访问 8000：

```powershell
New-NetFirewallRule -DisplayName "DeepSeek Chat 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

如果使用反向代理（推荐），**不要**对公网开放 8000，只开放 80/443，并在 `.env` 中设置 `HOST=127.0.0.1`。

---

## 8. HTTPS 反向代理（生产必做）

Cookie 只有在 HTTPS 下才能真正防窃听；同时浏览器录音（语音输入）也要求 HTTPS。

### 8.1 方案 A：Caddy（最省事，自动申请证书）

1. 下载 `caddy_windows_amd64.exe` 放到 `C:\tools\caddy\caddy.exe`。
2. 新建 `C:\tools\caddy\Caddyfile`：

```caddyfile
chat.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

`flush_interval -1` 保证 SSE 流式输出不被缓冲。

3. 修改 `server\.env`：

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

4. 用 NSSM 把 Caddy 也注册成服务：

```powershell
nssm install Caddy C:\tools\caddy\caddy.exe
nssm set Caddy AppParameters "run --config C:\tools\caddy\Caddyfile"
nssm set Caddy AppDirectory C:\tools\caddy
nssm set Caddy Start SERVICE_AUTO_START
nssm start Caddy
```

5. DNS 将 `chat.example.com` 解析到服务器，放行 80/443；Caddy 会自动申请并续期证书。

### 8.2 方案 B：IIS + ARR

1. 安装 IIS、**Application Request Routing (ARR)**、**URL Rewrite**。
2. 在 IIS 管理器 → 服务器节点 → *Application Request Routing Cache* → *Server Proxy Settings*：
   - 勾选 **Enable proxy**；
   - **Preserve host header** 勾选；
   - **Response buffer limit** 设为 `0`（否则流式输出会被整段缓冲）。
3. 站点根目录放 `web.config`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <rewrite>
      <rules>
        <rule name="DeepSeekProxy" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:8000/{R:1}" />
        </rule>
      </rules>
    </rewrite>
    <serverRuntime uploadReadAheadSize="104857600" />
  </system.webServer>
</configuration>
```

4. 站点绑定 HTTPS 证书，设置 `COOKIE_SECURE=true`、`HOST=127.0.0.1`，重启服务。
5. 若聊天不是“逐字输出”而是等很久一次性出现，说明响应仍被缓冲；优先改用 Caddy 或 Nginx。

### 8.3 方案 C：Nginx

```nginx
server {
    listen 443 ssl;
    server_name chat.example.com;

    ssl_certificate     C:/certs/chat.crt;
    ssl_certificate_key C:/certs/chat.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 600s;
    }
}
```

> 反向代理必须保留 `Host` 头，否则后端的同源 CSRF 校验会拒绝写操作（返回“跨站请求被拒绝”）。

---

## 9. 数据、备份与升级

### 9.1 数据文件

| 路径 | 说明 |
| --- | --- |
| `server\data\chat.db` | 用户、会话快照、消息投影、配置（含加密后的 API Key）、审计日志；默认上限 4GB |
| `server\data\files\` | 文件缓冲区（图片/PDF/文档等）；默认上限 2GB，超出按最久未使用删除 |
| `server\data\text\` | 纯文本/代码文件；默认上限 1GB，独立于文件缓冲区 |
| `server\data\secret.key` | 加密 API Key 的密钥文件，**必须与数据库一起备份**；丢失后需重新填写 API Key |
| `server\data\initial-admin-password.txt` | 首次随机密码，登录后建议删除 |
| `server\data\server.log` | 应用日志（10MB 轮转，保留 5 份） |

### 9.2 备份

建议每天停服或用 SQLite 在线备份：

```powershell
Stop-Service DeepSeekChat
Copy-Item D:\apps\deepseek-chat\server\data\chat.db D:\backup\chat-$(Get-Date -Format yyyyMMdd).db
Copy-Item D:\apps\deepseek-chat\server\data\secret.key D:\backup\secret-$(Get-Date -Format yyyyMMdd).key
Copy-Item D:\apps\deepseek-chat\server\data\files D:\backup\files-$(Get-Date -Format yyyyMMdd) -Recurse -ErrorAction SilentlyContinue
Copy-Item D:\apps\deepseek-chat\server\data\text D:\backup\text-$(Get-Date -Format yyyyMMdd) -Recurse -ErrorAction SilentlyContinue
Start-Service DeepSeekChat
```

> 也可以使用 `sqlite3 chat.db ".backup D:\backup\chat.db"` 做在线热备。

### 9.3 升级

1. 停止服务；
2. 备份 `server\data`；
3. 覆盖 `web\`、`server\app\`、`server\pages\`、`requirements.txt`（**不要覆盖 `data` 和 `.env`**）；
4. 如前端有改动，按 4.1 重新构建并替换 `web\`；
5. `server\.venv\Scripts\python.exe -m pip install -r requirements.txt`；
6. 启动服务，访问 `/healthz` 验证。

> 旧版本数据库会在启动时自动补充同步所需字段，无需手工迁移。

### 9.4 修改配置后

修改 `.env` 后需要重启服务（NSSM：`Restart-Service DeepSeekChat`）。修改后台 API Key / 用户无需重启，立即生效。

---

## 10. 安全加固建议

1. 首次登录后立即修改管理员密码（后台左下角“修改我的密码”），并删除 `initial-admin-password.txt`。
2. 生产环境必须启用 HTTPS，并设置 `COOKIE_SECURE=true`。
3. 不要把 8000 端口直接暴露到公网；只暴露反向代理的 443。
4. 给不同使用者创建独立账号，不要共用管理员；管理员数量保持最少。
5. 定期轮换 DeepSeek API Key（后台“清除”后重新保存即可）。
6. 服务账号使用低权限账户（NSSM 默认 LocalSystem，可改为专用账户；需保证对 `server\data` 有读写权限）。
7. 定期检查后台“审计日志”，关注异常登录与用户变更。
8. 数据库和 `secret.key` 的备份应加密保存。
9. 管理员可在后台查看全部用户的会话内容，请在组织内明确告知并控制管理员数量。
10. 关注 Python / 依赖的安全更新，升级后在测试环境验证再上线。

---

## 11. 常见问题

**Q1：启动报 `ModuleNotFoundError: No module named 'app'`**
必须从 `server` 目录启动（`cd server`），或使用 `server\start.bat`。

**Q2：登录后一直跳回登录页**
- 检查服务器时间是否准确；
- 反向代理是否保留 `Host` 头；
- HTTPS 下 `COOKIE_SECURE` 是否为 `true`（HTTP 访问时为 `false`）；
- 浏览器是否禁用了 Cookie。

**Q3：聊天提示“服务器尚未配置 DeepSeek API Key”**
管理员登录后台“API Key”页保存并测试连接。

**Q4：聊天报 502 / 无法连接**
服务器无法出站访问 `api.deepseek.com`，检查代理、DNS、防火墙；或确认 `DEEPSEEK_BASE_URL` 未被改错。

**Q5：回复不是流式的，很久后一次性出现**
反向代理开启了响应缓冲。Caddy 加 `flush_interval -1`；IIS 关闭 response buffering；Nginx 设置 `proxy_buffering off`。

**Q6：写操作返回“跨站请求被拒绝”**
反向代理没有保留 `Host` 头，导致 `Origin` 与 `Host` 不一致。按上文配置 `proxy_set_header Host $host` / IIS 勾选 Preserve host header；或在 `.env` 里配置 `ALLOWED_ORIGINS=https://你的域名`。

**Q7：8000 端口被占用**
`netstat -ano | findstr :8000` 找到 PID，确认没有重复启动服务/计划任务/前台进程。

**Q8：数据库 locked**
不要用多进程（`--workers`）运行；确认没有同时运行两份服务。

**Q9：修改前端后页面没变化**
静态资源文件名带 hash，浏览器会缓存旧文件。强制刷新（Ctrl+F5）或重启服务后再试。

**Q10：控制台中文乱码**
PowerShell 执行 `chcp 65001`；不影响 Web 页面与日志文件（UTF-8）。

**Q11：换一台电脑登录后看不到会话**
- 确认浏览器控制台没有 `/api/sync/*` 的 4xx/5xx；
- 确认两边的账号是同一个用户（管理员可在后台确认会话归属）；
- 首次打开会先合并再显示，网络慢时稍等；
- 若服务端数据被“清空全部对话”或历史导入替换过，旧会话不会恢复。

**Q12：附件在其他设备只显示文件名、无法预览**
附件原文件只保存在上传它的浏览器 IndexedDB 中，服务端同步的是附件元数据与 DeepSeek 文件 ID。要跨设备预览，需要另行实现对象存储/附件上传。

**Q13：前端改了源码但页面没变**
按 4.1 重新构建并替换 `web/`；构建产物文件名带 hash，替换后强制刷新或重启服务。

---

## 12. 附录：主要接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/login` | 公开 | 登录页 |
| POST | `/api/auth/login` | 公开 | 登录（限流） |
| POST | `/api/auth/logout` | 登录 | 退出 |
| GET | `/api/auth/me` | 登录 | 当前用户 |
| POST | `/api/auth/change-password` | 登录 | 修改自己的密码 |
| GET | `/models` | 登录 | 代理 DeepSeek 校验 |
| POST | `/anthropic/v1/messages` | 登录 | 聊天（落库 + 流式代理） |
| POST | `/anthropic/v1/files` | 登录 | 文件上传代理 |
| POST | `/v1/tools/asr` / `/tts` | 登录 | 语音代理 |
| GET | `/api/my/conversations` | 登录 | 本人服务端会话 |
| GET | `/api/my/conversations/{id}` | 登录 | 本人会话详情 |
| POST | `/api/files` | 登录 | 上传文件到服务器缓冲区（所有文件单文件统一 32MB） |
| GET | `/api/files/{id}` | 登录 | 下载文件（默认 attachment） |
| GET | `/api/files/{id}/text` | 登录 | 文本文件预览 |
| GET | `/api/sync/sessions` | 登录 | 会话同步列表 |
| GET | `/api/sync/sessions/{client_id}` | 登录 | 会话同步详情 |
| PUT | `/api/sync/sessions/{client_id}` | 登录 | 上传/更新会话快照 |
| PATCH | `/api/sync/sessions/{client_id}` | 登录 | 重命名同步会话 |
| DELETE | `/api/sync/sessions/{client_id}` | 登录 | 删除同步会话 |
| POST | `/api/sync/sessions/replace` | 登录 | 整体替换（历史导入/清空） |
| GET | `/admin` | 管理员 | 管理后台 |
| GET/POST | `/api/admin/users` | 管理员 | 用户列表 / 新建 |
| PATCH/DELETE | `/api/admin/users/{id}` | 管理员 | 修改 / 删除 |
| GET/PUT/DELETE | `/api/admin/apikey` | 管理员 | Key 状态（脱敏）/ 保存 / 清除 |
| POST | `/api/admin/apikey/test` | 管理员 | 测试 Key |
| GET | `/api/admin/conversations` | 管理员 | 全部会话 |
| GET | `/api/admin/conversations/{id}` | 管理员 | 会话详情 |
| GET | `/api/admin/audit` | 管理员 | 审计日志 |
| GET/PUT | `/api/admin/storage` | 管理员 | 存储用量/上限 |
| GET | `/api/admin/storage/files` | 管理员 | 文件列表 |
| DELETE | `/api/admin/storage/files/{id}` | 管理员 | 删除文件 |
| POST | `/api/admin/storage/vacuum` | 管理员 | 释放数据库空间 |
| DELETE | `/api/admin/conversations/{id}` | 管理员 | 删除会话 |
| GET | `/healthz` | 公开 | 健康检查 |
