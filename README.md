# GuGuAI 私有化聊天服务

[English](README.en.md) | 简体中文

在官方 [`deepseek-ai/chat-demo`](https://github.com/deepseek-ai/chat-demo) 源码基础上改造，增加 **登录鉴权、服务端 API Key 托管、会话跨设备同步、用户管理后台**，可直接部署到 Windows Server / Linux 在内网使用。

> 界面品牌：**GuGuAI**，副标题 **Powered by Deepseek.**

> 部署步骤见 **[docs/windows-server-deploy.md](docs/windows-server-deploy.md)**；NAT + HTTP + 公网 IP 直连见 **[docs/windows-server-nat-http.md](docs/windows-server-nat-http.md)**；实现细节见 **[docs/implementation-notes.md](docs/implementation-notes.md)**。

---

## 功能

- **登录后才能聊天**：`/login` 登录页，服务端会话 Cookie（HttpOnly、SameSite=Lax，可吊销）。
- **服务端鉴权**：页面、聊天接口、同步接口、管理接口全部由后端校验；普通用户无法访问管理后台，也无法读取 API Key。
- **API Key 只在服务器**：Key 使用 Fernet 加密后存入 SQLite，管理员后台只显示脱敏值；聊天/文件/语音请求由服务器统一携带 Key 转发到 DeepSeek。
- **消息先上传服务器，再调用 API**：发送前先把完整会话快照推送到服务器，服务器再请求 DeepSeek；助手回复在流式结束后写入快照并投影入库。
- **大聊天渐进渲染**：超长会话首屏只渲染最近 24 条消息，向上/向下滚动分批加载；搜索和跳转条会按需展开目标消息窗口，避免一次渲染全部 Markdown。
- **加载提示**：每次切换会话都先显示加载动画，再延迟挂载消息列表，低端设备也不会“点完像卡住”。
- **自定义网页**：主标题/副标题/品牌色，标识支持文字、图片（PNG/JPG/GIF/WebP/BMP，≤1MB）或关闭。
- **本地数据按账号隔离**：IndexedDB 使用 `chat-demo-u{用户ID}` 独立库；认证完成前使用临时库，绝不读取旧版共享库，避免不同账号互相看到/上传聊天记录。
- **跨设备删除同步**：登录时如服务端会话列表已不存在该会话（其他设备删除），本机会自动清理对应缓存和本地文件；本机真正新建、尚未推送成功的会话会被保留并补推。
- **会话与文件漫游**：登录同一账号，任何设备都能看到服务器上的会话、消息和附件；本地优先，本地没有时按需从服务端下载。
- **删除即清理**：删除会话会同时删除服务端快照、投影会话/消息、附件引用，以及不再被其他会话使用的缓冲区和文本区文件。
- **文件缓冲区**：图片/文档先上传到服务器（所有文件单文件统一 32MB，默认总上限 2GB，超出按最旧文件删除）；本地没有再回源到服务器预览。
- **文本预览**：聊天框和消息里的 txt/cpp/py/js/json 等文件可直接打开预览，服务端文本区独立计容（默认 1GB）。
- **图片预览**：聊天消息和输入框里的图片点击后在网页内弹窗预览，使用 data URL 加载，不依赖浏览器 blob 链接或新标签页；与 HTML/SVG 预览共用同一弹窗界面。
- **HTML/SVG 运行**：模型生成的 HTML/SVG 代码块和上传的 .html/.svg 文件可在隔离 iframe 中预览。
- **长文本转附件**：粘贴超过 2000 字符/80 行的内容时立即转为以第一行命名的 `.txt` 附件，输入框保留需求；手动输入的超长文本在发送时转为 `.txt` 附件并提示“请阅读附件中的内容。”，按阅读附件方式交给模型；删除附件、取消发送或离开页面时会清理已上传草稿，服务端也会定期清理超过 24 小时的孤立上传。
- **聊天多选**：多选入口在侧边栏底部设置按钮左侧；进入后“选择对话”旁有圆形全选按钮，可全选/取消全选并批量删除。
- **提问跳转**：点击右侧提问标记可定位旧消息；跳转时只展开目标附近窗口并显示定位提示，不会把整段很长的聊天一次性渲染。
- **聊天记录搜索**：侧边栏/折叠栏的搜索按钮打开顶部搜索框，只读本地缓存（IndexedDB/内存），按字面匹配用户与助手文字，展示标题、片段、日期，点击跳转到对应消息；不请求后端。
- **轻量动画**：会话/页面切换淡入、设置/预览等弹窗缩放淡入、侧边栏收起展开滑动过渡，尊重 `prefers-reduced-motion`。
- **快速跳转条**：右侧默认显示短横线，悬停展开可滚动的问题列表；点击问题文字或短横线跳转。
- **唯一管理员规则**：系统只允许一个管理员；管理员可修改自己的用户名，不能删除、降级或禁用自己；如果管理员被删除，启动时自动按 `ADMIN_PASSWORD`（未配置则随机生成）恢复 admin。
- **批量管理**：后台会话记录/存储管理表格支持全选/多选批量删除；聊天侧边栏支持多选会话批量删除（暂不实现置顶）。
- **用户管理**：新建/编辑/停用/删除用户、重置自己的用户名、重置密码，登录失败限流；可标记“下次登录必须修改密码”，被标记账号登录后强制弹窗改密并重新登录，改密前无法访问聊天/管理接口。
- **审计日志**：登录、用户管理、API Key 变更均记录。
- **历史导入/导出**：导入 ZIP 时会把归档里的本地原件上传到服务器；导出提供“兼容官方客户端”选项，只写图片原件，生成的 ZIP 可直接被 DeepSeek 官方客户端导入。导出时本地未缓存详情会从服务端按需回源，不再因“摘要存在但详情未加载”失败。
- **存储管理**：后台调整数据库/缓冲区/文本区上限（默认 4GB/2GB/1GB）、管理文件、删除会话；删除后自动执行增量 VACUUM 回收数据库页，后台仍可手动 VACUUM。SQLite 默认 20MB 页缓存（`cache_size=-20000`，约 5,120 个 4KB 页，每个连接独立），缓冲区和文本区超过上限时都会删除最旧文件。
- **暗黑模式**：登录页、管理后台、用户页右上角切换，默认跟随系统；聊天界面沿用原有主题设置。
- **账号入口**：聊天界面的账号/退出/管理后台入口统一放在设置中，并提供 `/ui/account` 用户页面。
- **保留原前端所有能力**：流式输出、深度思考、联网搜索、文件/图片、语音输入/朗读、消息分支、历史导入导出。

## 架构

```
浏览器（React + IndexedDB 缓存）
   │  Cookie 会话
   ▼
FastAPI (uvicorn)
   ├─ /login /logout /admin       登录页、管理后台
   ├─ /api/sync/*                 会话快照同步（含分支/附件元数据）
   ├─ /api/admin/*                用户、API Key、会话审计
   ├─ /models /anthropic/v1/*     携带服务端 Key 转发到 DeepSeek
   ├─ SQLite                      用户 / 会话快照 / 消息投影 / 配置 / 审计
   └─ /static/*                   源码构建后的前端（web/）
```

- 会话以 `session_snapshots` 表为准；同时投影到 `conversations` / `messages`，管理后台与会话记录页沿用原有视图。
- 聊天请求带 `X-Chat-Session-Id`，代理据此把助手回复归档到同一会话，不会重复建会话。

## 目录

```
chat_demo_score/chat-demo/   前端源码（React + TypeScript，已改造）
  src/services/chat/sync.ts  服务端会话同步
  src/services/serverSession.ts 当前用户/登录跳转
  rspack.config.ts           API 同源、服务端托管开关、开发代理
web/                         构建产物（由 chat-demo/dist 复制而来，后端实际提供）
server/                      后端服务
  app/                       路由与业务逻辑
  pages/                     登录页 / 管理后台 / 会话记录页
  run.py                     启动入口
  install.ps1                安装依赖
  .env.example               配置模板
tests/                       Mock 上游 + 端到端测试
docs/                        Windows Server 部署指南与实现说明
```

## 本地快速启动（开发）

```powershell
# 1. 构建前端（需要 Node.js 22.12+ 与 pnpm）
cd chat_demo_score/chat-demo
pnpm install
pnpm build
Remove-Item ..\..\web\* -Recurse -Force
Copy-Item .\dist\* ..\..\web -Recurse -Force

# 2. 启动后端
cd ..\..\server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env        # 设置 ADMIN_PASSWORD（留空则自动生成随机密码）
.\.venv\Scripts\python.exe run.py
```

打开 <http://localhost:8000/login>。首次启动会创建管理员：

- 配置了 `ADMIN_PASSWORD`：直接使用该密码；
- 未配置：随机密码写入 `server/data/initial-admin-password.txt` 并打印到日志。

登录后进入 `/admin`：配置 DeepSeek API Key → 测试连接 → 创建普通用户 → 返回聊天。

前端源码开发模式（需同时启动后端）：

```powershell
cd chat_demo_score/chat-demo
pnpm dev     # http://localhost:8090，/api、/anthropic 等自动代理到 127.0.0.1:8000
```

## 测试

```powershell
# 终端 1：Mock 上游（使用服务端虚拟环境）
.\server\.venv\Scripts\python.exe -m uvicorn mock_upstream:app --app-dir tests --port 18081

# 终端 2：测试实例
cd server
$env:DATA_DIR="./data-test"; $env:ADMIN_PASSWORD="admin12345"; $env:DEEPSEEK_BASE_URL="http://127.0.0.1:18081"
.\.venv\Scripts\python.exe run.py --port 18080

# 终端 3：端到端测试（回到项目根目录执行）
.\server\.venv\Scripts\python.exe tests\e2e_test.py
.\server\.venv\Scripts\python.exe tests\security_test.py
```

覆盖登录/限流、强制改密、RBAC、Key 脱敏、流式聊天、会话归并、快照同步、投影入库、整体替换/删除、会话头去重、文件缓冲/预览/配额、文本区淘汰、删除会话后的文件清理、共享附件引用、账号漫游、存储管理、CSRF、路径穿越、上传大小、前端构建产物检查等 150+ 项断言。

可选的浏览器冒烟测试（需要本机 Edge；无头环境无法启动时会跳过）：

```powershell
# 另开终端启动边缘浏览器
msedge --headless=new --remote-debugging-port=9222 --user-data-dir=$env:TEMP\edge-smoke
node tests\spa_smoke.mjs
```

## 同步机制与边界

1. **启动合并**：本地摘要与服务器摘要按会话 ID 合并；服务器较新的会话覆盖本地缓存，本地新增/较新的会话自动推送。
2. **保存即同步**：重命名、编辑、重新生成、切换分支、停止生成、导入历史都会触发快照推送（800ms 防抖）。
3. **发送前上传**：发起生成请求前会等待快照推送完成，确保服务器先有本轮消息；推送失败不阻塞聊天，后台重试。
4. **删除**：先删服务端再删本地，避免下次启动把会话拉回来；离线删除会提示失败。
5. **历史导入 / 删除全部**：本地替换成功后整体替换服务端记录；失败会置位并在下次启动重试。
6. **附件**：只同步附件元数据与 DeepSeek 文件 ID，原文件仍只保存在上传它的浏览器 IndexedDB 中；其他设备可看到附件名称与已上传文件引用。
7. **冲突**：多设备同时修改同一会话时按 `updatedAt` 后写覆盖，不做合并。
8. **管理员可见范围**：管理员可查看全部用户的会话内容（统一存储/审计的必然结果），请提前告知使用者。
9. SQLite + 单进程即可支撑中小团队；多实例/高并发建议换 PostgreSQL 并共享会话存储。

## 安全清单

- 修改默认管理员密码；
- 生产环境启用 HTTPS 并设置 `COOKIE_SECURE=true`；
- 不对公网暴露 8000，只暴露反向代理 443；
- 定期轮换 API Key、备份 `server/data/chat.db` 与 `secret.key`；
- 关注 `server/data/server.log` 与后台审计日志。

## Linux 部署

Windows Server 是主要支持平台（作者只在 Windows 上调试）。Linux 提供 `server/install.sh`、`server/start.sh`、systemd 示例和 [Linux 部署指南](docs/linux-deploy.md)，作为便利脚本，未持续测试。

## License

前端部分沿用上游 MIT License（见 `chat_demo_score/chat-demo/LICENSE`）；后端、部署脚本和项目文档使用 Apache License 2.0（见根目录 `LICENSE` 与 `NOTICE`）。
