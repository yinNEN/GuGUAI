English | [简体中文](implementation-notes.md)

# Implementation Notes

This document explains how the two requirements were implemented, where the key code lives, and the trade-offs involved, to support future maintenance and secondary development.

## Overall Structure

```
chat_demo_score/chat-demo (React source, refactored)
   ├─ src/services/chat/sync.ts        session snapshot sync
   ├─ src/services/chat/store.ts       session state, send, rename, delete, import
   ├─ src/services/serverSession.ts    current user, login redirect
   └─ rspack.config.ts                 same-origin API, server-managed switch, dev proxy
                    │  Cookie session
                    ▼
server/app (FastAPI)
   ├─ sync_api.py     /api/sync/*       session snapshots
   ├─ chat_api.py     /anthropic/v1/*   chat proxy + message archiving
   ├─ admin_api.py    /api/admin/*      users and API keys
   ├─ auth_api.py     /api/auth/*       login, password change, current user
   └─ store.py        SQLite data access and projection
```

The frontend build output lives in `web/` and is served by the backend as static assets; `web/` is already included in this repository, so no Node.js is required on the server (unless you change the source and rebuild).

## Requirement 1: Sync Messages from the Server + Centralize DeepSeek Calls on the Server

### Send and Sync Flow

```
Browser
  │ ① Update the session locally (user message + pending assistant placeholder)
  │ ② await PUT /api/sync/sessions/{sessionId}   full session snapshot (including branches)
  ▼
FastAPI
  │ ③ Validate ownership and structure, write to session_snapshots
  │ ④ If the session state has finished (no pending/streaming), project to conversations / messages
  │ ⑤ The server reads the API key and POSTs to https://api.deepseek.com/anthropic/v1/messages
  │    The request carries X-Chat-Session-Id, so the proxy no longer recreates sessions by prefix
  ▼
DeepSeek SSE ──⑥ forwarded verbatim──> Browser (token-by-token output)
  │ ⑦ After the stream ends, the frontend pushes the final snapshot again
  ▼
Server-side projection: the admin session list / session history page shows full messages and token usage
```

Corresponding code:

| Step | Location |
| --- | --- |
| Session snapshot upload/fetch | `src/services/chat/sync.ts`, `server/app/sync_api.py` |
| Startup merge | `store.ts::initChat` → `sync.syncOnInit` |
| Push before sending | `store.ts::streamReply` → `sync.pushNow` |
| Local change triggers sync | `store.ts::persist` → `sync.schedulePush` |
| Chat proxy | `server/app/chat_api.py::messages` |
| Snapshot projection | `server/app/store.py::upsert_snapshot` / `_project_session` |

### Client Merge Rules (`syncOnInit`)

1. Read the local IndexedDB summaries;
2. Fetch `GET /api/sync/sessions`;
3. Merge by session ID:
   - Local only → load the local details and queue them for push to the server (history from older clients migrates automatically);
   - Server only → write the summary locally and fetch details on demand when the session is opened;
   - Both → the newer `updatedAt` wins; if the server is newer, discard the stale local details; if local is newer, push.
4. Return the merged summary list.

### How Changes Are Synced

- **Rename**: loaded sessions are pushed via `persist`; unloaded sessions use `PATCH /api/sync/sessions/{id}` directly.
- **Delete**: call `DELETE /api/sync/sessions/{id}` before deleting locally, so the session is not pulled back on the next startup; a network failure reports that the deletion failed.
- **Edit prompt / regenerate / switch branch / stop generation**: all update the session and trigger a snapshot push.
- **History import / delete all**: after the local `commitStaging` succeeds, call `POST /api/sync/sessions/replace` to replace everything; on failure, write `chat-demo:sync-replace-pending` and retry on the next startup.
- **Page hidden/closed**: `flushAll()` uses `keepalive` to send pending snapshots as far as possible.

### Server-Side Storage

A new `session_files` table records the attachments referenced by each session (`user_id + client_id + file_id`). When a session is deleted, when everything is replaced, or when a session is deleted from the admin console, the snapshot and references are deleted first, and then any buffer/text files no longer referenced by any session are removed; on startup, older databases automatically backfill the references from snapshots.

A new `session_snapshots` table:

| Field | Description |
| --- | --- |
| `user_id` + `client_id` | Composite primary key; the frontend session UUID |
| `title` / `data` | Title and the full session JSON (messages, branches, attachment metadata, state, usage) |
| `created_at` / `updated_at` | Millisecond timestamps used for list ordering and conflict resolution |

The data is also projected into the legacy tables to keep the admin/history pages compatible:

- `conversations` gains a `client_id` column (unique index on `user_id + client_id`); title, model, message count, and timestamps come from the snapshot;
- `messages` stores each message's role, content blocks, token usage, and fingerprint.

Projection runs only when the snapshot contains no `pending`/`streaming` assistant message, avoiding repeated rewrites of the entire message during streaming. Older clients do not send `X-Chat-Session-Id` and still use the original "user message prefix matching" archiving logic, preserving compatibility.

### Attachments

What is synced is the attachment metadata (name, size, MIME, whether it is an image, DeepSeek `file_id`) and the reference relationships; the original file Blob is still kept in the IndexedDB of the browser that uploaded it. Other devices can see attachment names/uploaded references, but cannot preview them without the local original.

## Requirement 2: Login, Backend Authorization, and Admin API Key Configuration

### Login and Sessions

- `/login` is a standalone login page (`server/pages/login.html`) that calls `POST /api/auth/login`.
- Passwords use bcrypt (cost 12); when the username does not exist, a dummy hash is still performed to reduce timing side channels.
- On successful login a 32-byte random token is generated and only its sha256 is stored in the database; the cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` where applicable.
- Failed logins are rate-limited per IP (default: 10 attempts per 5 minutes).
- Changing a password, disabling a user, or deleting a user revokes all of that user's sessions.

### Permissions

| Scope | Rule |
| --- | --- |
| Pages | `/` and `/ui/history` require login; `/admin` requires an administrator; unauthenticated requests redirect to `/login` |
| Chat proxy | `/models`, `/anthropic/v1/*`, `/v1/tools/*` require login; 401 responses carry `X-Session-Expired: 1` |
| Sync API | `/api/sync/*` requires login, and only the current user's own data is accessible |
| Admin API | `/api/admin/*` requires an administrator, otherwise 403 |
| User data | `/api/my/*` returns only the current user's own sessions |

### API Key

- Administrators save the key in `/admin`; the key is encrypted with Fernet and written to the `settings` table.
- No endpoint lets a regular user read the key; admin endpoints return only a masked value of the form `sk-abc****wxyz`.
- The chat, file, and ASR/TTS proxies all ignore the `x-api-key` sent by the browser and use the server-side key instead.
- The `DEEPSEEK_API_KEY` environment variable is supported (the admin configuration takes precedence).
- `data/secret.key` (or `APP_SECRET`) is used for encryption/decryption; if it is lost or changed, the key must be entered again.

### User Management

- Create users (optionally requiring a password change at the next login), change roles, enable/disable, reset passwords, and delete. Accounts marked accordingly are forced to change their password after login; until then the middleware allows only `/ui/account`, `/api/auth/me`, `/api/auth/change-password`, and `/api/auth/logout`.
- You cannot delete, disable, or demote yourself; the system always keeps at least one active administrator.
- All sensitive operations are written to the audit log.

## Frontend Source Changes

| File | Change |
| --- | --- |
| `rspack.config.ts` | `apiOrigin` defaults to empty (same origin); new `serverManaged` switch and `__SERVER_MANAGED__`; dev server proxies to 8000; `pnpm build` is cross-platform |
| `src/services/chat/sync.ts` | New: list/detail/push/rename/delete/replace-all, debounce and retry |
| `src/services/chat/store.ts` | Startup merge, on-demand loading from the server, save/rename/delete/import triggering sync, `pushNow` before sending, `X-Chat-Session-Id` |
| `src/services/chat/db.ts` | New `saveSessionSummary`, `loadAllSessions` |
| `src/services/serverSession.ts` | New: `/api/auth/me`, redirect to login on 401, logout |
| `src/services/api/client.ts` | Requests carry cookies; 401 redirects to login; chat requests carry the session header |
| `src/services/settings.ts` | In server-managed mode the API key is fixed to a placeholder value |
| `src/components/settings/*` | Hide the key input and show "managed centrally by the server" |
| `src/components/sider/Sider.tsx` | Show the current user, admin console, and logout at the bottom |
| `src/i18n/*` | New account-related copy |


## Administrators and Bulk Operations

- Only one administrator is allowed: before creating/promoting an administrator, check `count_admins(active_only=False) == 0`; you cannot delete yourself, and you cannot demote/disable yourself or the last active administrator. Administrators can change their own username. At startup, if an old database contains multiple administrators, the one matching ADMIN_USERNAME (or the earliest enabled one) is kept and the rest are automatically demoted to regular users.
- Startup fallback: when the total number of administrators is 0, `ensure_bootstrap_admin()` creates `admin` with `ADMIN_PASSWORD` or a random password; if that username is already taken by a regular user, that user is promoted and the password is reset.
- The admin session/file tables gain a first-column checkbox, select-all, and bulk delete.
- Chat sidebar multi-select mode: select conversations, round checkboxes, delete at the bottom; exiting restores the settings button, with a fade-in/slide-up animation.

## Cross-Device Deletion Sync

After the server session list is returned successfully, sessions that exist locally but not on the server are treated as deleted on another device, and their local details/summaries/files are cleaned up. Sessions created on this device but not yet pushed successfully are protected by a `local-new:<dbName>` marker and will not be deleted by mistake; the marker is cleared after a successful push. Local data is kept when the server list request fails or a history-import replacement has not completed yet (`REPLACE_PENDING_KEY=1`).

## Per-Account Local Data Isolation

In older versions all accounts shared a single IndexedDB `chat-demo`, so switching accounts merged/uploaded the previous account's local sessions into the new account. Now `db.ts` uses `chat-demo-u{userId}` after `/api/auth/me` succeeds; before authentication completes it always uses `chat-demo-upending`, and a network failure does not fall back to the old shared database. Switching accounts requires a full-page login, and the old database is no longer opened.

## Large-Chat Performance and Custom Branding

- **Progressive rendering**: `MessageList` renders only the messages in the window, initially the most recent 24; scrolling up/down loads 16 more each time. The search/jump bar repositions the window near the target message via the `chat:reveal-message` event.
- **Session-switch loading state**: `App` records the current activeId in `visibleFor`; after a switch it shows a spinner for at least 140ms before mounting the message list, avoiding a lack of feedback during re-rendering.
- **Custom site**: the `settings` table stores the title/subtitle/brand color/logo mode; the logo supports text, image, or off. Images are limited to PNG/JPG/GIF/WebP/BMP, ≤1MB, validated by magic bytes, stored under `data/branding/icon.*`, and exposed via the public endpoint `/api/public/branding/icon`; they are managed through the admin API, and regular users receive 403. Brand text is always rendered as plain text.
- **Backend**: fixed a resource leak where `db()` connections were not closed; added a `messages(created_at)` index, `synchronous=NORMAL`, and a 20MB page cache.

## Chat History Search and Animations

- **Search entry points**: search buttons are available at the top of the expanded sidebar, in the collapsed rail, and in the mobile top bar; clicking one opens a search panel centered at the top.
- **Local search**: reads only from `useChatStore().sessions`, `sessionCache`, and the IndexedDB `db.loadSession`, without calling a search endpoint; it matches only user `content` and assistant `text` blocks, using literal case-insensitive substring matching. Results show the session title, the matching snippet, and the date; clicking a result selects the session and scrolls to/highlights the corresponding `data-message-id`.
- **Animations**: page/session switches fade in over `0.32s`; the SPA and admin Modal use a `0.26s` overlay plus a `0.32s` scaled fade-in for the content; admin panel switches take `0.32s`; search result rows `0.24s`; desktop sidebar width/translation stays at `0.28s`; the mobile drawer slides in; all provide `prefers-reduced-motion` fallbacks.

## File Buffering, Text Preview, and Storage Management

- **Upload path**: the frontend sends `POST /api/files` to write the file to the server first, which returns a file ID for this service; when chatting, the server's `prepare_outbound_payload` then swaps that ID for a DeepSeek `file_id`, uploading it if necessary. This way the browser no longer calls the DeepSeek Files API directly.
- **Buffer**: non-text files are stored in `server\data\files\`, with a default cap of 2GB; when exceeded, the oldest files are deleted by `created_at`. The per-file limit is 32MB.
- **Unified limit**: all uploaded attachments (text, code, images, PDF, Office) share a 32MB per-file limit; the input drag-and-drop hint, the over-limit message, and the server-side limit stay consistent.
- **Text area**: plain text such as txt/cpp/py/js/json is stored in `server\data\text\` under a separate quota (default 1GB) that does not count toward the buffer; when exceeded, the oldest files are deleted the same way. The admin storage manager can view/delete them.
- **Preview**: `useFileView` reads the original from IndexedDB first and falls back to `/api/files/{id}`; text files additionally go through `/api/files/{id}/text` and are previewed in a modal. Local first, saving bandwidth.
- **Image preview**: clicking an image in a message or draft opens the `FilePreview` modal; the local original/IndexedDB is preferred, otherwise it downloads from `/api/files/{id}`. Images are displayed via `FileReader` data URLs rather than `URL.createObjectURL` blob links, avoiding blob preview issues in some browsers; the modal reuses the title, close button, and dimensions of the HTML/SVG preview.
- **Generated-content preview**: html/svg code blocks in Markdown carry a "Preview" button and run in an iframe with `sandbox="allow-scripts allow-forms"`; uploaded .html/.svg files likewise support a "Preview/Source" toggle.
- **Long text**: when `onPaste` detects content longer than 2000 characters or 80 lines, it is immediately converted to a `.txt` attachment; the filename is taken from the first line with illegal characters sanitized, and the input box is left available so the user can keep typing their request. Overly long text entered manually is converted to a `.txt` attachment on send, the input box is replaced with "Please read the content in the attachment.", and it is handed to the model as an attachment to read.
- **Storage management**: the admin console can adjust the database (default 4GB), buffer (2GB), and text-area (1GB) limits, view files, delete files/sessions, and run `VACUUM`. When the buffer or text area exceeds its limit, the oldest files are deleted by `created_at`; once the database reaches its limit, chat/sync requests return 507. SQLite page cache is set with `PRAGMA cache_size=-20000` to about 20MB per connection (roughly 5,120 4KB pages), not shared process-wide; peak memory scales with concurrent connections. New databases use `auto_vacuum=INCREMENTAL`; old databases are converted once at startup. Deletes run incremental VACUUM, and audit logs are retained for 180 days (max 100k rows).
- **Jump bar**: short horizontal lines are shown on the right by default; hovering expands a scrollable list of prompts, and clicking a prompt's text or a line jumps to it, with the current reading position highlighted.
- **History import/export**: by default the export includes all local originals; when "Compatible with the official client" is checked, the export is exactly the same as the original export (only image originals are written), importable by the official DeepSeek client. Import accepts both official and server-side archives. After import, `uploadImportedFiles` uploads the originals to the server (images to the buffer, text to the text area); files over 32MB or hitting a 413/507 quota are skipped without affecting the local import; snapshots are then synced to the server.
- **Account and dark mode**: the chat account entry moved into Settings; a new `/ui/account` user page was added. The login page/admin console/user page share `theme.js`, which follows the system by default and can be toggled manually.

## Known Limitations

1. **Attachments load on demand**: metadata syncs with the session, and originals are downloaded from the server on demand; the buffer evicts least-recently-used files, and evicted files are no longer guaranteed to be previewable.
2. **Conflict policy**: when multiple devices modify the same session concurrently, the last write wins by `updatedAt`, with no merging.
3. **Offline deletion**: deletion must delete on the server first, so it fails when offline; other changes queue and retry, and are pushed with `keepalive` when the page closes.
4. **Administrators can see all sessions**: this is an unavoidable consequence of unified storage/auditing and must be communicated clearly within the organization.
5. **SQLite is single-host, single-process**: multi-instance deployment requires switching to PostgreSQL and moving sessions/rate limiting to shared storage.
6. **Legacy database compatibility**: at startup the `client_id` column and index are added to `conversations` automatically, and old sessions remain viewable in the admin console.

## Prompt Jump

The jump bar no longer polls scroll position; instead, after receiving `chat:reveal-message`, MessageList turns off sticky, sets a fixed render window around the target, and scrolls once layout completes. A lightweight waiting indicator is shown while locating (at least 150ms), preventing the ResizeObserver from pulling the view back to the bottom while the old message window expands.
