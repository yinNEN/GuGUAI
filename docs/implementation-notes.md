# 改造实现说明

[English](implementation-notes.en.md) | 简体中文

本文说明两项需求如何落地、关键代码位置与取舍，便于后续维护或二次开发。

## 总体结构

```
chat_demo_score/chat-demo（React 源码，已改造）
   ├─ src/services/chat/sync.ts        会话快照同步
   ├─ src/services/chat/store.ts       会话状态、发送、改名、删除、导入
   ├─ src/services/serverSession.ts    当前用户、登录跳转
   └─ rspack.config.ts                 API 同源、服务端托管开关、开发代理
                    │  Cookie 会话
                    ▼
server/app（FastAPI）
   ├─ sync_api.py     /api/sync/*       会话快照
   ├─ chat_api.py     /anthropic/v1/*   聊天代理 + 消息归档
   ├─ admin_api.py    /api/admin/*      用户与 API Key
   ├─ auth_api.py     /api/auth/*       登录、改密、当前用户
   └─ store.py        SQLite 数据访问与投影
```

前端构建产物在 `web/`，由后端以静态资源方式提供；`web/` 已包含在本仓库中，服务器上不需要 Node.js（除非改源码后重新构建）。

## 需求 1：从服务器同步消息 + 服务端统一调用 DeepSeek

### 发送与同步流程

```
浏览器
  │ ① 本地更新会话（用户消息 + pending 助手占位）
  │ ② await PUT /api/sync/sessions/{sessionId}   完整会话快照（含分支）
  ▼
FastAPI
  │ ③ 校验归属与结构，写入 session_snapshots
  │ ④ 若会话状态已结束（无 pending/streaming），投影到 conversations / messages
  │ ⑤ 服务端取 API Key，POST https://api.deepseek.com/anthropic/v1/messages
  │    请求头带 X-Chat-Session-Id，代理不再按前缀重复建会话
  ▼
DeepSeek SSE ──⑥ 原样转发──> 浏览器（逐字输出）
  │ ⑦ 流结束后前端再次推送最终快照
  ▼
服务端投影：后台会话列表 / 会话记录页可看到完整消息与 token 用量
```

对应代码：

| 环节 | 位置 |
| --- | --- |
| 会话快照上传/拉取 | `src/services/chat/sync.ts`、`server/app/sync_api.py` |
| 启动合并 | `store.ts::initChat` → `sync.syncOnInit` |
| 发送前推送 | `store.ts::streamReply` → `sync.pushNow` |
| 本地修改触发同步 | `store.ts::persist` → `sync.schedulePush` |
| 聊天代理 | `server/app/chat_api.py::messages` |
| 快照投影 | `server/app/store.py::upsert_snapshot` / `_project_session` |

### 客户端合并规则（`syncOnInit`）

1. 读取本地 IndexedDB 摘要；
2. 拉取 `GET /api/sync/sessions`；
3. 按会话 ID 合并：
   - 只有本地有 → 读取本地详情，排队推送到服务端（旧版客户端的历史会自动迁移）；
   - 只有服务端有 → 写入本地摘要，打开会话时按需拉取详情；
   - 两边都有 → `updatedAt` 较新者为准；服务端较新时丢弃本地旧详情，本地较新时推送。
4. 返回合并后的摘要列表。

### 修改如何同步

- **重命名**：已加载会话走 `persist` 推送；未加载会话直接 `PATCH /api/sync/sessions/{id}`。
- **删除**：先 `DELETE /api/sync/sessions/{id}` 再删本地，避免下次启动把会话拉回来；网络失败会提示删除失败。
- **编辑提问 / 重新生成 / 切换分支 / 停止生成**：都会更新会话并触发快照推送。
- **历史导入 / 删除全部**：本地 `commitStaging` 成功后调用 `POST /api/sync/sessions/replace` 整体替换；失败时写 `chat-demo:sync-replace-pending`，下次启动重试。
- **页面隐藏/关闭**：`flushAll()` 用 `keepalive` 尽量把待推送快照发出。

### 服务端存储

新增 `session_files` 表记录每个会话引用的附件（`user_id + client_id + file_id`）。删除会话/整体替换/后台删除会话时，会先删快照与引用，再删除不再被任何会话引用的缓冲区/文本文件；旧数据库启动时自动根据快照回填引用。

新增 `session_snapshots` 表：

| 字段 | 说明 |
| --- | --- |
| `user_id` + `client_id` | 联合主键，前端会话 UUID |
| `title` / `data` | 标题与完整会话 JSON（消息、分支、附件元数据、状态、用量） |
| `created_at` / `updated_at` | 毫秒时间戳，用于列表排序与冲突判断 |

同时投影到旧表，保持后台/历史页兼容：

- `conversations` 增加 `client_id` 列（唯一索引 `user_id + client_id`），标题、模型、消息数、时间来自快照；
- `messages` 保存每条消息的角色、内容块、token 用量与指纹。

投影只在快照中没有 `pending`/`streaming` 的助手消息时执行，避免流式过程中反复重写整段消息。旧版客户端不发送 `X-Chat-Session-Id`，仍走原来的「用户消息前缀匹配」归档逻辑，保持兼容。

### 附件

同步的是附件元数据（名称、大小、MIME、是否图片、DeepSeek `file_id`）与引用关系；原文件 Blob 仍保存在上传它的浏览器 IndexedDB 中。其他设备可看到附件名称/已上传引用，但没有本地原件时不能预览。

## 需求 2：登录、后端鉴权、API Key 后台配置

### 登录与会话

- `/login` 为独立登录页（`server/pages/login.html`），调用 `POST /api/auth/login`。
- 密码 bcrypt（cost 12）；用户名不存在时也执行一次假哈希，降低时间侧信道。
- 登录成功生成 32 字节随机令牌，数据库只存 sha256；Cookie 为 `HttpOnly`、`SameSite=Lax`、按需 `Secure`。
- 登录失败按 IP 限流（默认 5 分钟 10 次）。
- 改密、禁用、删除用户都会吊销该用户全部会话。

### 权限

| 范围 | 规则 |
| --- | --- |
| 页面 | `/`、`/ui/history` 需登录；`/admin` 需管理员；未登录跳 `/login` |
| 聊天代理 | `/models`、`/anthropic/v1/*`、`/v1/tools/*` 需登录，401 带 `X-Session-Expired: 1` |
| 同步 API | `/api/sync/*` 需登录，且只能访问当前用户自己的数据 |
| 管理 API | `/api/admin/*` 需管理员，否则 403 |
| 用户数据 | `/api/my/*` 只返回当前用户自己的会话 |

### API Key

- 管理员在 `/admin` 保存；Key 用 Fernet 加密后写入 `settings` 表。
- 普通用户没有任何接口能读取 Key；管理接口只返回 `sk-abc****wxyz` 形式的脱敏值。
- 聊天、文件、ASR/TTS 代理全部忽略浏览器传来的 `x-api-key`，改用服务端 Key。
- 支持环境变量 `DEEPSEEK_API_KEY`（后台配置优先）。
- `data/secret.key`（或 `APP_SECRET`）用于加解密；丢失或变更后需重新填写 Key。

### 用户管理

- 新建用户（可要求下次登录修改密码）、修改角色、启用/禁用、重置密码、删除。被标记账号登录后强制弹出改密窗口；改密前中间件只放行 `/ui/account`、`/api/auth/me`、`/api/auth/change-password`、`/api/auth/logout`。
- 不允许删除/禁用/降级自己；系统始终保留至少一个启用的管理员。
- 所有敏感操作写入审计日志。

## 前端源码改动

| 文件 | 改动 |
| --- | --- |
| `rspack.config.ts` | `apiOrigin` 默认空（同源）；新增 `serverManaged` 开关与 `__SERVER_MANAGED__`；开发服务器代理到 8000；`pnpm build` 跨平台 |
| `src/services/chat/sync.ts` | 新增：列表/详情/推送/改名/删除/整体替换、防抖与重试 |
| `src/services/chat/store.ts` | 启动合并、按需从服务端加载、保存/改名/删除/导入触发同步、发送前 `pushNow`、`X-Chat-Session-Id` |
| `src/services/chat/db.ts` | 新增 `saveSessionSummary`、`loadAllSessions` |
| `src/services/serverSession.ts` | 新增：`/api/auth/me`、401 跳登录、退出 |
| `src/services/api/client.ts` | 请求带 Cookie；401 跳登录；聊天请求带会话头 |
| `src/services/settings.ts` | 服务端托管模式下 API Key 固定为占位值 |
| `src/components/settings/*` | 隐藏 Key 输入，显示“由服务器统一管理” |
| `src/components/sider/Sider.tsx` | 底部显示当前用户、管理后台、退出登录 |
| `src/i18n/*` | 新增账号相关文案 |


## 管理员与批量操作

- 只允许一个管理员：创建/提升管理员前检查 `count_admins(active_only=False) == 0`；不能删除自身，不能降级/禁用自己或最后一个启用管理员。管理员可以修改自己的用户名。启动时如发现旧库存在多个管理员，保留 ADMIN_USERNAME/最早启用的一个，其余自动降级为普通用户。
- 启动兜底：`ensure_bootstrap_admin()` 在管理员总数为 0 时，用 `ADMIN_PASSWORD` 或随机密码创建 `admin`；若该用户名已被普通用户占用，则提升并重置密码。
- 后台会话/文件表格增加首列复选框、全选和批量删除。
- 聊天侧边栏多选模式：选择对话、圆形复选框、底部删除；退出后恢复设置按钮，使用淡入/上移动画。

## 跨设备删除同步

服务端会话列表成功返回后，本地存在但服务端不存在的会话视为已在其他设备删除，清理本地详情/摘要/文件。本机新建但尚未推送成功的会话通过 `local-new:<dbName>` 标记保护，不会被误删，推送成功后清除标记。服务端列表请求失败或历史导入替换尚未完成（`REPLACE_PENDING_KEY=1`）时保留本地数据。

## 账号本地数据隔离

旧版本所有账号共用一个 IndexedDB `chat-demo`，切换账号会把前一个账号的本地会话合并/上传到新账号。现在 `db.ts` 在 `/api/auth/me` 成功后使用 `chat-demo-u{userId}`；认证完成前固定使用 `chat-demo-upending`，网络失败也不会回退到旧共享库。切换账号必须整页登录，旧库不再打开。

## 大聊天性能与自定义品牌

- **渐进渲染**：`MessageList` 只渲染窗口内消息，初始最近 24 条；向上/向下滚动各加载 16 条。搜索/跳转条通过 `chat:reveal-message` 事件把窗口重定位到目标消息附近。
- **切会话加载态**：`App` 用 `visibleFor` 记录当前 activeId，切换后先显示 spinner 至少 140ms，再挂载消息列表，避免重渲染期间无反馈。
- **自定义网页**：`settings` 表保存标题/副标题/品牌色/标识模式；标识支持文字、图片、关闭。图片仅允许 PNG/JPG/GIF/WebP/BMP，≤1MB，按 magic bytes 校验，存 `data/branding/icon.*`，公开接口 `/api/public/branding/icon`；管理员 API 管理，普通用户返回 403。品牌文字均按纯文本渲染。
- **后端**：修复 `db()` 连接未关闭的资源泄漏；增加 `messages(created_at)` 索引、`synchronous=NORMAL` 和 20MB 页缓存。

## 聊天记录搜索与动画

- **搜索入口**：展开侧边栏顶部、折叠栏、移动端顶栏均有搜索按钮；点击打开顶部居中的搜索面板。
- **本地搜索**：只从 `useChatStore().sessions`、`sessionCache` 和 IndexedDB `db.loadSession` 读取，不调用搜索接口；仅匹配用户 `content` 与助手 `text` 块，字面大小写不敏感包含匹配。结果展示会话标题、匹配片段、日期；点击后选择会话并滚动/高亮对应 `data-message-id`。
- **动画**：页面/会话切换淡入 `0.32s`；SPA 与管理后台 Modal 遮罩 `0.26s` + 内容 `0.32s` 缩放淡入；管理后台面板切换 `0.32s`；搜索结果行 `0.24s`；桌面侧边栏宽度/位移保持 `0.28s`；移动端抽屉滑入；均提供 `prefers-reduced-motion` 降级。

## 文件缓冲、文本预览与存储管理

- **上传路径**：前端 `POST /api/files` 先把文件写入服务器，返回本服务文件 ID；聊天时服务端 `prepare_outbound_payload` 再把 ID 换成 DeepSeek `file_id`，必要时补传。这样浏览器不再直接调用 DeepSeek Files API。
- **缓冲区**：非文本文件存 `server\data\files\`，默认上限 2GB；超过时按 `created_at` 删除最旧文件。单文件上限 32MB。
- **统一上限**：所有上传附件（文本、代码、图片、PDF、Office）单文件均为 32MB；输入框拖拽提示、超限提示、服务端限制保持一致。
- **文本区**：txt/cpp/py/js/json 等纯文本存 `server\data\text\`，单独限额（默认 1GB），不计入缓冲区，超过时同样按最旧文件删除；后台存储管理可查看/删除。
- **预览**：`useFileView` 先读 IndexedDB 原件，没有则回源 `/api/files/{id}`；文本文件另走 `/api/files/{id}/text`，在弹窗中预览。本地优先，节省带宽。
- **图片预览**：消息和草稿里的图片点击后打开 `FilePreview` 弹窗；本地原件/IndexedDB 优先，没有则从 `/api/files/{id}` 下载。图片通过 `FileReader` 转 data URL 显示，不使用 `URL.createObjectURL` 的 blob 链接，避免部分浏览器 blob 预览异常；弹窗沿用 HTML/SVG 预览的标题、关闭按钮和尺寸。
- **生成内容预览**：Markdown 的 html/svg 代码块带“预览”按钮，在 `sandbox="allow-scripts allow-forms"` 的 iframe 中运行；上传的 .html/.svg 文件同样支持“预览/源码”切换。
- **长文本**：`onPaste` 检测到超过 2000 字符或 80 行的内容时立即转为 `.txt` 附件，文件名取第一行并清洗非法字符，输入框保留用户继续输入需求。手动输入的超长文本在发送时转换为 `.txt` 附件，输入框替换为“请阅读附件中的内容。”，按阅读附件方式交给模型。
- **存储管理**：后台可调整数据库（默认 4GB）、缓冲区（2GB）、文本区（1GB）上限，查看文件、删除文件/会话、执行 `VACUUM`。缓冲区和文本区超过上限时都按 `created_at` 删除最旧文件；数据库达到上限后聊天/同步返回 507。
- **跳转条**：右侧默认显示短横线；悬停展开可滚动的问题列表，点击问题文字或短横线跳转，当前阅读位置高亮。
- **历史导入/导出**：默认导出包含所有本地原件；勾选“兼容官方客户端”时与原版导出完全一致（只写图片原件），可被 DeepSeek 官方客户端导入。导入同时接受官方与服务端两种归档。导入完成后 `uploadImportedFiles` 会把原件上传到服务器（图片进缓冲区，文本进文本区），单文件 32MB、配额 413/507 时跳过，不影响本地导入；之后同步快照到服务端。
- **账号与暗黑模式**：聊天账号入口移入设置；新增 `/ui/account` 用户页。登录页/后台/用户页共用 `theme.js`，默认跟随系统并可手动切换。

## 已知边界

1. **附件按需加载**：元数据随会话同步，原文件按需从服务器下载；缓冲区按最久未使用淘汰，已淘汰的文件不再保证可预览。
2. **冲突策略**：多设备同时修改同一会话时按 `updatedAt` 后写覆盖，不做合并。
3. **离线删除**：删除需要先删服务端，断网时会失败；其余修改会排队重试并在页面关闭时 `keepalive` 推送。
4. **管理员可见全部会话**：这是统一存储/审计的必然结果，需在组织内明确告知。
5. **SQLite 单机单进程**：多实例部署需要换 PostgreSQL 并把会话/限流改为共享存储。
6. **旧数据库兼容**：启动时自动为 `conversations` 补 `client_id` 列与索引，旧会话仍可在后台查看。

## 提问跳转

跳转条不再轮询滚动；由 MessageList 在收到 `chat:reveal-message` 后关闭 sticky、围绕目标设置固定渲染窗口，并在布局完成后滚动。定位期间显示轻量等待提示（至少 150ms），避免旧消息窗口展开时 ResizeObserver 把视图拉回底部。
