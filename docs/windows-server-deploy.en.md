English | [简体中文](windows-server-deploy.md)

# Windows Server Deployment Guide

This document explains how to deploy this project on Windows Server 2019 / 2022 (x64) and complete the initialization of login authentication, API Key hosting, message persistence, file buffering, and the admin console.

> If your server is on a NAT intranet, is accessed directly via `http://public-IP:8000`, and has no domain name or HTTPS, read **[windows-server-nat-http.en.md](windows-server-nat-http.en.md)** as well.

---

## 0. What This Version Does

The upstream project [`deepseek-ai/chat-demo`](https://github.com/deepseek-ai/chat-demo) is a **purely static** chat frontend: the API Key lives in the browser's `localStorage`, the browser calls `https://api.deepseek.com` directly, and conversations exist only locally in the browser.

This repository reworks its **frontend source code** (`chat_demo_score/chat-demo`) directly and adds a server side. All the original features — streaming chat, deep thinking, web search, files/images, voice, message branching, and history import/export — are preserved.

| Capability | Implementation |
| --- | --- |
| Unified login | `/login` page; the server issues an HttpOnly session cookie (stored in SQLite, revocable at any time) |
| Backend authentication | Every page and API validates the session in middleware; admin APIs additionally validate the role |
| API Key hosting | The Key is encrypted and stored in the server's SQLite; chat, file, and voice requests are all forwarded by the server with the Key attached, so the browser never touches the real Key |
| Conversation sync | The frontend uploads/pulls complete conversation snapshots via `/api/sync/*` (including branches, renames, and attachment metadata); log in on another device and all conversations are there |
| Unified message storage | The conversation snapshot is pushed before sending, then the server calls DeepSeek; the assistant reply is written into the snapshot after streaming ends and projected into the backend conversation/message tables |
| File buffering | The browser uploads files to the server first (every file is capped at 32MB, 2GB of buffer by default, and overflow deletes the oldest files); during chat the server re-uploads them to DeepSeek |
| Text preview | Text files such as txt/cpp/py/js/json can be previewed directly in the chat box and inside messages; the server has a separate text area (1GB by default) |
| Storage management | The admin console manages database/buffer/text limits, files, conversation cleanup; deletes reclaim pages automatically and manual VACUUM remains available |
| User management | The admin console creates/edits/disables/deletes users, renames the administrator account, and resets passwords |
| Audit | Records logins, user management, API Key changes, and other operations |
| User-side history | `/ui/history` also lets users view all of their own conversations saved on the server |
| Account page | `/ui/account` shows account information and lets users change their password; the account entry point in the chat UI is unified under settings |
| Dark mode | The top-right corner of the login page, admin console, and user pages can toggle light/dark mode; by default it follows the system |

Directory structure:

```
deepseek_local/
├─ chat_demo_score/chat-demo/ # Frontend source (React + TypeScript, reworked)
│  ├─ src/services/chat/sync.ts        # Conversation sync
│  ├─ src/services/serverSession.ts    # Current user and login redirect
│  └─ rspack.config.ts                 # Same-origin API, server hosting switch, dev proxy
├─ web/                       # Frontend build output (copied from chat-demo/dist, served by the backend)
│  ├─ index.html
│  └─ static/
├─ server/                    # New backend (FastAPI)
│  ├─ app/                    #   Routes, auth, proxy, sync, storage
│  ├─ pages/                  #   Login page / admin console / conversation records page
│  ├─ run.py                  #   Startup entry point
│  ├─ install.ps1             #   One-click dependency install
│  ├─ start.bat               #   Foreground start
│  ├─ install-service-nssm.ps1
│  ├─ install-startup-task.ps1
│  ├─ requirements.txt
│  ├─ .env.example            #   Copy to .env and then modify
│  └─ data/                   #   Created at runtime: chat.db, secret.key, files/, text/, logs
├─ tests/                     # Mock upstream for integration testing + end-to-end tests
└─ docs/windows-server-deploy.md
```

---

## 1. Deployment Architecture

```
Browser ──HTTPS──> Caddy / IIS / Nginx (optional, handles certificates)
                        │
                        ▼
              uvicorn 127.0.0.1:8000
                        │  carries the server-side API Key
                        ▼
              https://api.deepseek.com
```

- Production recommendation: the reverse proxy listens on 443, and the application listens only on `127.0.0.1:8000`.
- All business data lives in `server/data/chat.db` (SQLite, WAL mode).
- A single process is enough (sessions and rate limiting are both in-process); **do not** run multiple processes with `--workers`, otherwise SQLite writes and rate limiting become inconsistent.

---

## 2. Environment Requirements

- Windows Server 2019 / 2022, x64
- Python **3.12** (3.10+ works, 3.12 recommended)
- Outbound access to `https://api.deepseek.com` (HTTPS 443)
- Administrator privileges (to install the service and open the firewall)
- Optional: a domain name + public/intranet DNS (for HTTPS)
- Optional: Node.js 22.12+ and pnpm (only needed to rebuild the frontend)

---

## 3. Install Python

1. Download the Windows x64 installer: <https://www.python.org/downloads/windows/> (choose the *Windows installer (64-bit)* for Python 3.12).
2. During installation, check **Add python.exe to PATH** and select *Install for all users*.
3. Open a new PowerShell and verify:

```powershell
python --version
# Python 3.12.x
```

---

## 4. Copy the Project

Copy the whole project directory to the server, for example:

```
D:\apps\deepseek-chat\
├─ chat_demo_score\chat-demo\   # Frontend source (only needed when rebuilding)
├─ web\                          # Built frontend output (served by the backend)
├─ server\
├─ tests\
└─ docs\
```

> Note: `web/` and `server/` must stay at the same directory level; the backend reads frontend files from `..\web` by default (it can be overridden with `WEB_DIR`).

### 4.1 Rebuilding the frontend (only needed when you change the source)

The `web/` in the repository is already a built artifact, so you can deploy it directly. If you changed source under `chat_demo_score/chat-demo`, you need to rebuild:

1. Install **Node.js 22.12+** and **pnpm** on the build machine (`npm install -g pnpm`);
2. Run:

```powershell
cd D:\apps\deepseek-chat\chat_demo_score\chat-demo
pnpm install
pnpm build
Remove-Item ..\..\web\* -Recurse -Force
Copy-Item .\dist\* ..\..\web -Recurse -Force
```

You can also build into another directory and point `WEB_DIR` at it:

```ini
WEB_DIR=D:\apps\deepseek-chat\chat_demo_score\chat-demo\dist
```

> Build output filenames contain hashes; after replacing `web/`, restart the service and force-refresh the browser (Ctrl+F5).

---

## 5. Install Dependencies and Configure

Open PowerShell **as administrator**:

```powershell
cd D:\apps\deepseek-chat\server
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

The script will:

1. Check the Python version;
2. Create the `server\.venv` virtual environment;
3. Install `requirements.txt`;
4. Copy `.env` from `.env.example` if it does not exist;
5. Create `server\data`.

If the server has slow access to PyPI, configure a mirror first and then run it:

```powershell
python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

Then edit the configuration:

```powershell
notepad D:\apps\deepseek-chat\server\.env
```

Items you **must confirm**:

```ini
ADMIN_USERNAME=admin
# A strong password is recommended (at least 12 characters, with upper/lower case, digits, and symbols); leave empty to generate a random password automatically
ADMIN_PASSWORD=replace-with-a-strong-password
HOST=0.0.0.0
PORT=8000
COOKIE_SECURE=false          # change to true once you are on HTTPS
```

Leave the remaining items at their defaults. `ADMIN_PASSWORD` is only used to create the first administrator when the database contains no users.

---

## 6. First Start and Initialization

### 6.1 Start

Double-click `server\start.bat`, or in PowerShell:

```powershell
cd D:\apps\deepseek-chat\server
.\.venv\Scripts\python.exe run.py
```

When you see `Uvicorn running on http://0.0.0.0:8000`, startup succeeded.

- If `ADMIN_PASSWORD` is configured in `.env`: log in with it.
- If it is empty: the random password is written to `server\data\initial-admin-password.txt` and also printed to the console and to `data\server.log`.

Open in a browser:

```
http://server-IP:8000/login
```

### 6.2 Admin console initialization (administrator)

After logging in you land on `/admin` by default:

1. **API Key** page: paste the DeepSeek API Key (`sk-...`) → **Save** → **Test connection**.
   - The Key is encrypted and stored in `chat.db`; the page only shows a masked value (such as `sk-abc****wxyz`).
   - It can also be provided through the `DEEPSEEK_API_KEY` environment variable, but the value saved in the admin console takes precedence.
2. **User management**: create regular users (setting an initial password), or adjust existing accounts.
3. **Overview**: confirm that "API Key configured" and the user/conversation statistics are normal.
4. Click **Back to chat** in the lower-left corner to start chatting.
5. Regular users go straight to the chat page after logging in; `/admin` redirects them, and admin APIs return 403.

> The original frontend settings may still show an "API Key" edit box; that is the frontend's built-in UI. A Key entered by a regular user is not used by the server and does not leak; the server always uses the Key hosted in the admin console.

### 6.3 Verification checklist

| Check | Expected result |
| --- | --- |
| `http://IP:8000/healthz` | `{"ok":true,...}` |
| Visiting `/` while not logged in | 302 redirect to `/login` |
| Logging in with the correct account | Enters the chat page; the username appears in the lower-left corner |
| Sending a message | Streaming output; the conversation appears in the sidebar; refreshing `/ui/history` shows that conversation |
| Logging into the same account in another browser/device | The conversation and messages just created are visible |
| Renaming/deleting a conversation | Stays consistent after refreshing |
| A regular user visiting `/admin` | 302 redirect to `/ui/history` |
| A regular user visiting `/api/admin/apikey` | 403 |
| Administrator API Key page | Shows only the masked Key |
| Closing and reopening the browser | The session is still valid (7 days by default) |

---

## 7. Register as a Windows Service (start on boot)

### 7.1 Option A: NSSM (recommended)

1. Download NSSM: <https://nssm.cc/download>, extract `win64\nssm.exe`, and place it at `C:\tools\nssm\nssm.exe`.
2. Run as administrator:

```powershell
cd D:\apps\deepseek-chat\server
powershell -ExecutionPolicy Bypass -File .\install-service-nssm.ps1 -NssmPath C:\tools\nssm\nssm.exe
```

3. Common commands:

```powershell
Get-Service DeepSeekChat
Restart-Service DeepSeekChat
Stop-Service DeepSeekChat
Start-Service DeepSeekChat
```

Logs: `server\data\service-out.log`, `server\data\service-err.log`, `server\data\server.log`.

### 7.2 Option B: Scheduled task (no third-party tool required)

```powershell
cd D:\apps\deepseek-chat\server
powershell -ExecutionPolicy Bypass -File .\install-startup-task.ps1
```

The task runs `.venv\Scripts\python.exe run.py` as `SYSTEM` at boot (Session 0, no visible window), and logs are written to `data\server.log`.

- View: `Get-ScheduledTask -TaskName DeepSeekChat`
- Delete: `Unregister-ScheduledTask -TaskName DeepSeekChat -Confirm:$false`

> Choose one of the two options; do not enable both, otherwise they will fight over port 8000.

### 7.3 Firewall

If clients access port 8000 directly:

```powershell
New-NetFirewallRule -DisplayName "DeepSeek Chat 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

If you use a reverse proxy (recommended), do **not** expose port 8000 to the public internet; open only 80/443 and set `HOST=127.0.0.1` in `.env`.

---

## 8. HTTPS Reverse Proxy (required in production)

Only under HTTPS can cookies truly prevent eavesdropping; browser recording (voice input) also requires HTTPS.

### 8.1 Option A: Caddy (simplest, automatic certificate issuance)

1. Download `caddy_windows_amd64.exe` and place it at `C:\tools\caddy\caddy.exe`.
2. Create `C:\tools\caddy\Caddyfile`:

```caddyfile
chat.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

`flush_interval -1` ensures SSE streaming output is not buffered.

3. Modify `server\.env`:

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

4. Register Caddy as a service with NSSM as well:

```powershell
nssm install Caddy C:\tools\caddy\caddy.exe
nssm set Caddy AppParameters "run --config C:\tools\caddy\Caddyfile"
nssm set Caddy AppDirectory C:\tools\caddy
nssm set Caddy Start SERVICE_AUTO_START
nssm start Caddy
```

5. Resolve `chat.example.com` to the server in DNS and allow 80/443 through; Caddy automatically obtains and renews certificates.

### 8.2 Option B: IIS + ARR

1. Install IIS, **Application Request Routing (ARR)**, and **URL Rewrite**.
2. In IIS Manager → server node → *Application Request Routing Cache* → *Server Proxy Settings*:
   - Check **Enable proxy**;
   - Check **Preserve host header**;
   - Set **Response buffer limit** to `0` (otherwise streaming output gets buffered as a whole).
3. Place `web.config` in the site root:

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

4. Bind an HTTPS certificate to the site, set `COOKIE_SECURE=true` and `HOST=127.0.0.1`, and restart the service.
5. If chat does not output word by word but appears all at once after a long wait, the response is still being buffered; switch to Caddy or Nginx first.

### 8.3 Option C: Nginx

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

> The reverse proxy must preserve the `Host` header; otherwise the backend's same-origin CSRF check rejects write operations (returning "cross-site request rejected").

---

## 9. Data, Backup, and Upgrade

### 9.1 Data files

| Path | Description |
| --- | --- |
| `server\data\chat.db` | Users, conversation snapshots, message projections, configuration (including the encrypted API Key), audit logs; 4GB limit by default |
| `server\data\files\` | File buffer (images/PDFs/documents, etc.); 2GB limit by default, overflow deletes the least recently used |
| `server\data\text\` | Plain text/code files; 1GB limit by default, independent of the file buffer |
| `server\data\secret.key` | The key file used to encrypt the API Key; **it must be backed up together with the database**; if it is lost, the API Key has to be entered again |
| `server\data\initial-admin-password.txt` | The initial random password; it is recommended to delete it after logging in |
| `server\data\server.log` | Application log (10MB rotation, 5 files retained) |

### 9.2 Backup

It is recommended to stop the service daily, or to use a SQLite online backup:

```powershell
Stop-Service DeepSeekChat
Copy-Item D:\apps\deepseek-chat\server\data\chat.db D:\backup\chat-$(Get-Date -Format yyyyMMdd).db
Copy-Item D:\apps\deepseek-chat\server\data\secret.key D:\backup\secret-$(Get-Date -Format yyyyMMdd).key
Copy-Item D:\apps\deepseek-chat\server\data\files D:\backup\files-$(Get-Date -Format yyyyMMdd) -Recurse -ErrorAction SilentlyContinue
Copy-Item D:\apps\deepseek-chat\server\data\text D:\backup\text-$(Get-Date -Format yyyyMMdd) -Recurse -ErrorAction SilentlyContinue
Start-Service DeepSeekChat
```

> You can also use `sqlite3 chat.db ".backup D:\backup\chat.db"` for an online hot backup.

### 9.3 Upgrade

1. Stop the service;
2. Back up `server\data`;
3. Overwrite `web\`, `server\app\`, `server\pages\`, and `requirements.txt` (**do not overwrite `data` or `.env`**);
4. If the frontend changed, rebuild per 4.1 and replace `web\`;
5. Run `server\.venv\Scripts\python.exe -m pip install -r requirements.txt`;
6. Start the service and visit `/healthz` to verify.

> An older database is automatically extended with the fields needed for sync at startup; no manual migration is required.

### 9.4 After changing the configuration

After modifying `.env` you need to restart the service (NSSM: `Restart-Service DeepSeekChat`). Changing the API Key / users in the admin console takes effect immediately without a restart.

---

## 10. Security Hardening Recommendations

1. Change the administrator password immediately after the first login (the "Change my password" item in the lower-left corner of the admin console), and delete `initial-admin-password.txt`.
2. HTTPS must be enabled in production, with `COOKIE_SECURE=true`.
3. Do not expose port 8000 directly to the public internet; expose only the reverse proxy's 443.
4. Create separate accounts for different users; do not share the administrator account; keep the number of administrators to a minimum.
5. Rotate the DeepSeek API Key regularly (in the admin console, use "Clear" and then save it again).
6. Use a low-privilege account for the service (NSSM defaults to LocalSystem, but you can switch to a dedicated account; it must have read/write access to `server\data`).
7. Regularly check the "Audit log" in the admin console, paying attention to abnormal logins and user changes.
8. Backups of the database and `secret.key` should be stored encrypted.
9. Administrators can view the conversation content of all users in the admin console; make this clear within your organization and limit the number of administrators.
10. Keep up with security updates for Python and the dependencies, and verify in a test environment after upgrading before going live.

---

## 11. FAQ

**Q1: On startup, `ModuleNotFoundError: No module named 'app'`**
You must start from the `server` directory (`cd server`), or use `server\start.bat`.

**Q2: After logging in, it keeps redirecting back to the login page**
- Check whether the server time is accurate;
- Whether the reverse proxy preserves the `Host` header;
- Under HTTPS, whether `COOKIE_SECURE` is `true` (it should be `false` when accessed over HTTP);
- Whether the browser has cookies disabled.

**Q3: Chat says "the server has not configured a DeepSeek API Key"**
Log in as administrator, go to the "API Key" page in the admin console, save the Key, and test the connection.

**Q4: Chat returns 502 / cannot connect**
The server cannot reach `api.deepseek.com` outbound; check the proxy, DNS, and firewall; or confirm that `DEEPSEEK_BASE_URL` has not been set incorrectly.

**Q5: Replies are not streamed and appear all at once after a long delay**
The reverse proxy has response buffering enabled. For Caddy add `flush_interval -1`; for IIS disable response buffering; for Nginx set `proxy_buffering off`.

**Q6: Write operations return "cross-site request rejected"**
The reverse proxy does not preserve the `Host` header, so `Origin` and `Host` do not match. Configure `proxy_set_header Host $host` / check Preserve host header in IIS as described above; or set `ALLOWED_ORIGINS=https://your-domain` in `.env`.

**Q7: Port 8000 is already in use**
Use `netstat -ano | findstr :8000` to find the PID, and confirm that you are not starting the service/scheduled task/foreground process twice.

**Q8: Database is locked**
Do not run with multiple processes (`--workers`); confirm that two copies of the service are not running at the same time.

**Q9: The page does not change after modifying the frontend**
Static asset filenames contain hashes, so the browser caches the old files. Force-refresh (Ctrl+F5) or restart the service and try again.

**Q10: Garbled Chinese characters in the console**
Run `chcp 65001` in PowerShell; this does not affect web pages or log files (UTF-8).

**Q11: No conversations are visible after logging in on another computer**
- Confirm that the browser console shows no 4xx/5xx for `/api/sync/*`;
- Confirm that both sides are the same user (an administrator can confirm conversation ownership in the admin console);
- On first open, data is merged before it is displayed, so wait a moment on a slow network;
- If the server data has been replaced by "clear all conversations" or by a history import, the old conversations will not be restored.

**Q12: Attachments on other devices show only the filename and cannot be previewed**
Attachment source files are kept only in the IndexedDB of the browser that uploaded them; what the server syncs is the attachment metadata and the DeepSeek file IDs. Cross-device preview requires implementing object storage / attachment upload separately.

**Q13: The frontend source was changed but the page did not change**
Rebuild and replace `web/` per 4.1; build output filenames contain hashes, so force-refresh or restart the service after replacing.

---

## 12. Appendix: Main APIs

| Method | Path | Permission | Description |
| --- | --- | --- | --- |
| GET | `/login` | Public | Login page |
| POST | `/api/auth/login` | Public | Login (rate limited) |
| POST | `/api/auth/logout` | Signed in | Log out |
| GET | `/api/auth/me` | Signed in | Current user |
| POST | `/api/auth/change-password` | Signed in | Change own password |
| GET | `/models` | Signed in | Proxied DeepSeek validation |
| POST | `/anthropic/v1/messages` | Signed in | Chat (persist + streaming proxy) |
| POST | `/anthropic/v1/files` | Signed in | File upload proxy |
| POST | `/v1/tools/asr` / `/tts` | Signed in | Voice proxy |
| GET | `/api/my/conversations` | Signed in | Own server-side conversations |
| GET | `/api/my/conversations/{id}` | Signed in | Own conversation details |
| POST | `/api/files` | Signed in | Upload a file to the server buffer (every file is capped at 32MB) |
| GET | `/api/files/{id}` | Signed in | Download a file (attachment by default) |
| GET | `/api/files/{id}/text` | Signed in | Text file preview |
| GET | `/api/sync/sessions` | Signed in | Conversation sync list |
| GET | `/api/sync/sessions/{client_id}` | Signed in | Conversation sync details |
| PUT | `/api/sync/sessions/{client_id}` | Signed in | Upload/update a conversation snapshot |
| PATCH | `/api/sync/sessions/{client_id}` | Signed in | Rename a synced conversation |
| DELETE | `/api/sync/sessions/{client_id}` | Signed in | Delete a synced conversation |
| POST | `/api/sync/sessions/replace` | Signed in | Replace everything (history import/clear) |
| GET | `/admin` | Administrator | Admin console |
| GET/POST | `/api/admin/users` | Administrator | User list / create |
| PATCH/DELETE | `/api/admin/users/{id}` | Administrator | Modify / delete |
| GET/PUT/DELETE | `/api/admin/apikey` | Administrator | Key status (masked) / save / clear |
| POST | `/api/admin/apikey/test` | Administrator | Test the Key |
| GET | `/api/admin/conversations` | Administrator | All conversations |
| GET | `/api/admin/conversations/{id}` | Administrator | Conversation details |
| GET | `/api/admin/audit` | Administrator | Audit log |
| GET/PUT | `/api/admin/storage` | Administrator | Storage usage/limits |
| GET | `/api/admin/storage/files` | Administrator | File list |
| DELETE | `/api/admin/storage/files/{id}` | Administrator | Delete a file |
| POST | `/api/admin/storage/vacuum` | Administrator | Reclaim database space |
| DELETE | `/api/admin/conversations/{id}` | Administrator | Delete a conversation |
| GET | `/healthz` | Public | Health check |
