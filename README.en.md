# GuGuAI Private Chat Service

Built on the upstream [`deepseek-ai/chat-demo`](https://github.com/deepseek-ai/chat-demo), this project adds **login authentication, server-side API Key custody, cross-device session sync, and an admin console**. It can be deployed on Windows Server or Linux for internal use.

> Brand: **GuGuAI** — tagline **Powered by Deepseek.**

> Deployment: **[docs/windows-server-deploy.en.md](docs/windows-server-deploy.en.md)**; NAT + HTTP + public IP: **[docs/windows-server-nat-http.en.md](docs/windows-server-nat-http.en.md)**; implementation notes: **[docs/implementation-notes.en.md](docs/implementation-notes.en.md)**.

English | [简体中文](README.md)

---

## Features

- **Login required**: `/login`; server-side session cookie (HttpOnly, SameSite=Lax, revocable).
- **Server-side authorization**: pages, chat, sync, and admin APIs are all checked by the backend. Regular users cannot access the admin console or read the API Key.
- **API Key stays on the server**: encrypted with Fernet and stored in SQLite; the admin UI only shows a masked value. Chat/file/voice requests are forwarded to DeepSeek by the server.
- **Upload before calling the API**: the full session snapshot is pushed to the server before generation; assistant replies are written to the snapshot and projected into the database after streaming ends.
- **Progressive rendering for large chats**: only the latest 24 messages are rendered on first open; older/newer chunks load on demand. Search and the jump rail reveal only a window around the target message instead of rendering all Markdown at once.
- **Loading feedback**: every session switch shows a loading animation before mounting the message list, so low-end devices do not appear frozen.
- **Custom branding**: title/subtitle/brand color; mark can be text, an image (PNG/JPG/GIF/WebP/BMP, ≤1MB), or disabled.
- **Per-account local data isolation**: IndexedDB uses `chat-demo-u{userID}`; a temporary database is used before authentication, and the legacy shared database is never read. Different accounts cannot see or upload each other's chats.
- **Cross-device deletion sync**: if the server no longer contains a session at login (deleted on another device), the local cache and files are cleaned up. A genuinely new local session that has not been pushed yet is preserved and retried.
- **Session and file roaming**: sign in with the same account to see sessions, messages, and attachments stored on the server; local data is preferred, with on-demand download when missing.
- **Delete means cleanup**: deleting a session removes the server snapshot, projected conversation/messages, attachment references, and buffer/text files no longer used by other sessions.
- **File buffer**: images/documents are uploaded first (32MB per file, 2GB total by default; oldest files are evicted when over the limit); other devices can preview from the server.
- **Text preview**: txt/cpp/py/js/json and similar files in the composer or messages can be previewed; the server text area has a separate quota (1GB by default).
- **Image preview**: images in messages and the composer open in an in-page modal using data URLs, without blob URLs or new tabs; shares the same modal as HTML/SVG previews.
- **HTML/SVG execution**: model-generated HTML/SVG code blocks and uploaded `.html`/`.svg` files can be previewed in an isolated iframe.
- **Long text to attachment**: pasting more than 2000 characters/80 lines immediately converts the content to a `.txt` attachment named after its first line, while the composer keeps the prompt. Very long manually typed text is converted at send time and the model receives an instruction to read the attachment. Removing the attachment, cancelling, or leaving the page cleans up uploaded drafts; the server also purges unreferenced uploads older than 24 hours.
- **Chat multi-select**: the entry is to the left of Settings at the bottom of the sidebar. In selection mode, a circular Select all control appears next to “Select chats”; batch deletion is supported.
- **Question jump rail**: click a question marker on the right to jump to an old message. Only a nearby window is expanded and a locating hint is shown; the entire long chat is not rendered at once.
- **Chat search**: the search button in the sidebar/collapsed sidebar opens a top search box; it reads local cache only (IndexedDB/memory), matches user and assistant text literally, shows title/snippet/date, and jumps to the message. No backend request.
- **Lightweight animations**: fade-in for session/page changes, scale/fade for settings/preview modals, slide transition for the sidebar, with `prefers-reduced-motion` support.
- **Jump rail**: short bars on the right; hover expands a scrollable question list; click a question or bar to jump.
- **Single-admin rule**: only one administrator is allowed. The admin can rename themselves but cannot delete, demote, or disable themselves. If the admin is deleted, startup restores `admin` from `ADMIN_PASSWORD` (or a random password if unset).
- **Batch management**: admin conversation/storage tables support select-all, multi-select, and batch deletion; the chat sidebar supports batch deletion (pinning is not implemented yet).
- **User management**: create/edit/disable/delete users, rename your own username, reset passwords, login rate limiting; mark “must change password on next login”, forcing a modal and re-login before accessing chat/admin APIs.
- **Audit log**: login, user management, and API Key changes are recorded.
- **History import/export**: importing a ZIP uploads local originals to the server; export has an “official-compatible” mode that writes only image originals so the ZIP can be imported by the official DeepSeek client.
- **Storage management**: adjust database/buffer/text quotas (4GB/2GB/1GB by default), manage files, delete conversations; deletes trigger incremental VACUUM to return freed pages, and a manual VACUUM remains available. Buffers and text areas evict oldest files when over quota.
- **Dark mode**: login page, admin console, and account page support theme switching (follow system by default); the chat UI keeps its original theme setting.
- **Account entry**: account/logout/admin links are grouped under Settings in chat, with a `/ui/account` page.
- **All original frontend features retained**: streaming, deep thinking, web search, files/images, voice input/read-aloud, message branches, history import/export.

## Architecture

```
Browser (React + IndexedDB cache)
   │  Cookie session
   ▼
FastAPI (uvicorn)
   ├─ /login /logout /admin       login page, admin console
   ├─ /api/sync/*                 session snapshot sync (branches/attachment metadata)
   ├─ /api/admin/*                users, API Key, conversation audit
   ├─ /models /anthropic/v1/*     forward to DeepSeek with server-side Key
   ├─ SQLite                      users / session snapshots / message projection / config / audit
   └─ /static/*                   built frontend (web/)
```

- `session_snapshots` is authoritative; data is projected into `conversations` / `messages` for the existing admin/conversation views.
- Chat requests carry `X-Chat-Session-Id`, so the proxy archives assistant replies into the same session instead of creating duplicates.

## Layout

```
chat_demo_score/chat-demo/   frontend source (React + TypeScript, adapted)
  src/services/chat/sync.ts  server session sync
  src/services/serverSession.ts current user/login redirect
  rspack.config.ts           same-origin API, server-managed switch, dev proxy
web/                         build output (copied from chat-demo/dist; served by backend)
server/                      backend service
  app/                       routes and business logic
  pages/                     login / admin / conversation history pages
  run.py                     entry point
  install.ps1                dependency installer (Windows)
  .env.example               config template
tests/                       mock upstream + end-to-end tests
docs/                        Windows Server deployment and implementation notes
```

## Local quick start (development)

```powershell
# 1. Build frontend (Node.js 22.12+ and pnpm)
cd chat_demo_score/chat-demo
pnpm install
pnpm build
Remove-Item ..\..\web\* -Recurse -Force
Copy-Item .\dist\* ..\..\web -Recurse -Force

# 2. Start backend
cd ..\..\server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env        # set ADMIN_PASSWORD (blank = random password)
.\.venv\Scripts\python.exe run.py
```

Open <http://localhost:8000/login>. First start creates the admin:

- `ADMIN_PASSWORD` set: that password is used;
- not set: a random password is written to `server/data/initial-admin-password.txt` and printed in the log.

After login go to `/admin`: configure the DeepSeek API Key → test connection → create regular users → return to chat.

Frontend dev mode (backend must also run):

```powershell
cd chat_demo_score/chat-demo
pnpm dev     # http://localhost:8090; /api and /anthropic proxy to 127.0.0.1:8000
```

## Tests

```powershell
# Terminal 1: mock upstream (server venv)
.\server\.venv\Scripts\python.exe -m uvicorn mock_upstream:app --app-dir tests --port 18081

# Terminal 2: test instance
cd server
$env:DATA_DIR="./data-test"; $env:ADMIN_PASSWORD="admin12345"; $env:DEEPSEEK_BASE_URL="http://127.0.0.1:18081"
.\.venv\Scripts\python.exe run.py --port 18080

# Terminal 3: end-to-end tests (from project root)
.\server\.venv\Scripts\python.exe tests\e2e_test.py
.\server\.venv\Scripts\python.exe tests\security_test.py
```

Covers login/rate limiting, forced password change, RBAC, Key masking, streaming chat, session merge, snapshot sync, projection, whole-history replace/delete, session-header deduplication, file buffer/preview/quota, text-area eviction, file cleanup after session deletion, shared attachment references, account roaming, storage management, CSRF, path traversal, upload limits, frontend build checks and 150+ assertions.

Optional browser smoke test (local Edge required; skipped when headless browser cannot start):

```powershell
# start Edge in another terminal
msedge --headless=new --remote-debugging-port=9222 --user-data-dir=$env:TEMP\edge-smoke
node tests\spa_smoke.mjs
```

## Sync behavior and boundaries

1. **Startup merge**: local and server summaries merge by session ID; newer server sessions replace local cache; new/newer local sessions are pushed.
2. **Save triggers sync**: rename, edit, regenerate, branch switch, stop generation, and history import push snapshots (800ms debounce).
3. **Upload before send**: generation waits for the snapshot push so the server has the current turn first; a failed push does not block chat and is retried in the background.
4. **Delete**: delete server first, then local, so the session is not pulled back at next startup; offline deletion reports failure.
5. **History import / delete all**: local replacement followed by whole-server replacement; failure is retried at next startup.
6. **Attachments**: only attachment metadata and DeepSeek file IDs are synced; originals stay in the uploading browser's IndexedDB. Other devices see attachment names and uploaded file references.
7. **Conflicts**: simultaneous edits on multiple devices use last-write-wins by `updatedAt`; no merge.
8. **Admin visibility**: admins can view all users' conversations (inherent to centralized storage/audit); inform users in advance.
9. SQLite + a single process is enough for small/medium teams; for multiple instances/high concurrency, use PostgreSQL and shared session storage.

## Security checklist

- Change the default admin password;
- Enable HTTPS in production and set `COOKIE_SECURE=true`;
- Do not expose port 8000 publicly; expose only the reverse proxy on 443;
- Rotate the API Key and back up `server/data/chat.db` and `secret.key`;
- Monitor `server/data/server.log` and the admin audit log.

## Linux deployment

Windows Server is the primary supported platform (the author only debugs on Windows). Linux convenience files: `server/install.sh`, `server/start.sh`, a systemd example, and the [Linux deployment guide](docs/linux-deploy.md). They are not continuously tested.

## License

The frontend retains the upstream MIT License (see `chat_demo_score/chat-demo/LICENSE`); the backend, deployment scripts, and project documentation use Apache License 2.0 (see root `LICENSE` and `NOTICE`).
