# Windows Server：NAT + HTTP + 公网 IP 直接访问部署指南

[English](windows-server-nat-http.en.md) | 简体中文

本文针对以下场景：

- 服务部署在内网 Windows Server，例如 `192.168.1.50:8000`；
- 公网出口经过路由器/防火墙 NAT；
- 对外使用 `http://公网IP:8000` 直接访问；
- 不使用域名、不使用反向代理、不配置 HTTPS 证书。

> 先完成主文档 [windows-server-deploy.md](windows-server-deploy.md) 的安装、`.env`、管理员初始化等步骤，再做本文的 NAT 和升级配置。

---

## 1. 部署拓扑

```
公网用户
   │  http://203.0.113.10:8000
   ▼
路由器 / 防火墙（NAT 端口转发）
   │  203.0.113.10:8000 → 192.168.1.50:8000
   ▼
Windows Server 192.168.1.50
   │  uvicorn 0.0.0.0:8000
   ▼
DeepSeek API（服务器出站 443）
```

需要保证：

1. 服务器的内网 IP **固定**（DHCP 保留或静态 IP）；
2. Windows 防火墙放行 8000；
3. 路由器把公网 8000 转发到服务器内网 8000；
4. 服务器能出站访问 `https://api.deepseek.com`；
5. 公网 IP 不是运营商 CGNAT 地址（见第 4 节）。

---

## 2. Windows Server 配置

### 2.1 固定内网 IP

在“网络连接 → 属性 → IPv4”设置静态地址，例如：

```
IP:      192.168.1.50
掩码:    255.255.255.0
网关:    192.168.1.1
DNS:     192.168.1.1 / 223.5.5.5
```

### 2.2 放行防火墙

管理员 PowerShell：

```powershell
New-NetFirewallRule -DisplayName "GuGuAI Chat 8000" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
```

如果只想允许内网/特定网段访问，可加 `-RemoteAddress 192.168.1.0/24`，但 NAT 转发后来源地址通常是路由器内网地址，需按实际网络调整。

### 2.3 配置 `.env`

```ini
HOST=0.0.0.0
PORT=8000
COOKIE_SECURE=false
ADMIN_PASSWORD=请换成强密码
```

HTTP 直连时 `COOKIE_SECURE` 必须为 `false`，否则浏览器不会保存登录 Cookie。

### 2.4 启动并验证

```powershell
cd D:\apps\deepseek-chat\server
.\start.bat
```

服务器本机访问：

```text
http://127.0.0.1:8000/healthz
http://192.168.1.50:8000/login
```

内网其他电脑访问 `http://192.168.1.50:8000/login`，确认能打开后再做端口转发。

### 2.5 注册为服务（推荐）

```powershell
.\install-service-nssm.ps1 -NssmPath C:\tools\nssm\nssm.exe
```

或使用计划任务：

```powershell
.\install-startup-task.ps1
```

---

## 3. 路由器 NAT 端口转发

不同路由器界面名称不同，通常在：

- “端口转发 / Port Forwarding”
- “虚拟服务器 / Virtual Server”
- “NAT / 端口映射”
- “防火墙 → 端口映射”

添加一条 TCP 规则：

| 项目 | 值 |
| --- | --- |
| 外部端口 | 8000（若运营商封锁 80/443/8000，可换 8080、8443 等） |
| 内部 IP | 192.168.1.50 |
| 内部端口 | 8000 |
| 协议 | TCP |

保存后，在外网用手机流量访问：

```text
http://公网IP:8000/healthz
```

返回 `{"ok":true,...}` 即成功。

常见问题：

- **打不开**：确认公网 IP 正确、规则协议是 TCP、Windows 防火墙放行、服务器服务正在运行；
- **只有内网能开**：路由器没有生效/NAT 回环（hairpin）不支持，外网测试请用手机 4G/5G，不要用同一内网 Wi-Fi；
- **端口被运营商封锁**：换 8080/18000 等端口，同时修改 `.env` 的 `PORT`、防火墙和端口转发；
- **公网 IP 会变**：使用 DDNS（花生壳、No-IP、Cloudflare DDNS 等）绑定域名，再用域名访问。

---

## 4. 判断是否处于 CGNAT（重要）

如果路由器 WAN 口 IP 与“我的公网 IP”不一致，或者 WAN IP 是 `10.x`、`100.64.x`、`172.16~31.x`、`192.168.x`，通常处于运营商大内网（CGNAT），**端口转发不会生效**。

排查：

1. 路由器状态页查看 WAN IP；
2. 在服务器上访问 `https://ipinfo.io/ip` 或搜索引擎“IP”查看公网 IP；
3. 两者不一致 → CGNAT。

CGNAT 下的可用方案（任选）：

- **IPv6**：向运营商申请公网 IPv6，路由器放行 8000，使用 `http://[IPv6]:8000`；
- **FRP / nps**：用一台有公网 IP 的云服务器做中转；
- **Cloudflare Tunnel**：免费、自带 HTTPS，不需要公网 IP 和端口转发，推荐；
- **Tailscale / ZeroTier**：组建虚拟内网，只有加入网络的设备能访问，安全性最好；
- 联系运营商申请公网 IP。

---

## 5. HTTP 直连的安全限制与缓解

HTTP 下登录密码、会话 Cookie、聊天内容、上传文件都在公网明文传输，可能被同一网络中的攻击者窃听。请至少做到：

1. 管理员和用户使用**独立强密码**，不要与其他网站相同；
2. 不要把 8000 端口暴露给所有人；能限制来源 IP 就限制；
3. 定期查看后台“审计日志”，发现异常登录立即改密/禁用；
4. 控制用户数量，离职人员及时禁用；
5. 定期备份 `server\data`；
6. 不要把 API Key 填到浏览器设置里（本版本 Key 只在服务端）；
7. 如果条件允许，优先改用 **Cloudflare Tunnel / Tailscale / FRP + HTTPS**，或申请域名后用 Caddy 自动证书。

> 本版本所有 API Key 都在服务端，浏览器只拿到占位值，因此即使 HTTP 被窃听也不会泄露 DeepSeek Key；但登录凭据和聊天内容仍应视为可能泄露。

---

## 6. 如果之后想上 HTTPS（可选）

直接用 `http://公网IP` 无法申请受信任证书（证书必须绑定域名）。可选：

### 6.1 域名 + Caddy（推荐）

1. 域名 A 记录指向公网 IP；
2. 路由器转发 80/443 到服务器；
3. 安装 Caddy，`Caddyfile`：

```caddyfile
chat.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

4. `.env` 改为：

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

5. 防火墙只放行 80/443，不再对公网开放 8000。

### 6.2 无法开放 80/443 时

- Cloudflare Tunnel：域名接入 Cloudflare，本机运行 `cloudflared tunnel`，自动 HTTPS；
- FRP + 云服务器：云服务器 443 终止 TLS，转发到内网；
- Tailscale/ZeroTier：不公开到公网，成员通过虚拟内网 IP 访问，流量加密。

### 6.3 自签名证书

可以给 IP 签自签名证书，但浏览器会显示“不安全”警告，且需要每台客户端安装信任，维护成本高，不推荐给多人使用。

---

## 7. 从旧版本升级（HTTP/NAT 场景）

### 7.1 升级前备份

```powershell
Stop-Service DeepSeekChat
$stamp = Get-Date -Format yyyyMMdd-HHmm
New-Item -ItemType Directory -Force D:\backup\$stamp | Out-Null
Copy-Item D:\apps\deepseek-chat\server\data\chat.db D:\backup\$stamp\
Copy-Item D:\apps\deepseek-chat\server\data\secret.key D:\backup\$stamp\
Copy-Item D:\apps\deepseek-chat\server\data\files D:\backup\$stamp\files -Recurse -ErrorAction SilentlyContinue
Copy-Item D:\apps\deepseek-chat\server\data\text D:\backup\$stamp\text -Recurse -ErrorAction SilentlyContinue
Copy-Item D:\apps\deepseek-chat\server\data\branding D:\backup\$stamp\branding -Recurse -ErrorAction SilentlyContinue
```

### 7.2 替换文件

保留以下内容不动：

- `server\data\`（数据库、密钥、文件缓冲、文本文件）
- `server\.env`

覆盖：

- `web\`
- `server\app\`
- `server\pages\`
- `server\requirements.txt`

### 7.3 安装新增依赖

新版本增加了 `python-multipart`（文件上传），必须执行：

```powershell
cd D:\apps\deepseek-chat\server
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 7.4 启动并检查

> 新版本启动时会自动创建 `session_files` 表，并根据现有会话快照回填附件引用；不需要手工迁移数据库。

```powershell
Start-Service DeepSeekChat
# 或 .\start.bat
```

访问：

```text
http://127.0.0.1:8000/healthz
http://公网IP:8000/login
```

### 7.5 新版本存储说明

| 路径 | 内容 | 默认上限 |
| --- | --- | --- |
| `server\data\chat.db` | 用户、会话快照、消息投影、配置 | 4GB（后台可调） |
| `server\data\files\` | 文件缓冲区（图片、PDF、Word 等） | 2GB（后台可调，超出按最久未使用删除） |
| `server\data\text\` | 纯文本/代码文件 | 1GB（后台可调，独立于缓冲区） |

管理后台 → “存储管理” 可以：

- 调整数据库、缓冲区、文本区上限（单位 MB）；
- 查看文件列表并删除；
- 删除会话；
- 执行 `VACUUM` 释放数据库空间。

数据库达到上限后，聊天/同步会返回“数据库空间已满”，需要管理员删除不用的会话后释放空间。

### 7.6 端口/转发不需要变化

新版本没有改变监听端口和路径，路由器 NAT、Windows 防火墙规则保持原样即可。如果 `.env` 中 `PORT` 改过，记得同步修改端口转发。

---

## 8. 更新后验收清单

| 检查项 | 期望结果 |
| --- | --- |
| `http://公网IP:8000/healthz` | `{"ok":true}` |
| `/login` 页面 | 显示 GuGuAI，右上角有明暗模式按钮 |
| 登录后发送文字 | 正常流式回复，侧边栏出现会话 |
| 上传图片/PDF | 上传成功，消息里可预览缩略图/文件卡片 |
| 上传 txt/cpp | 点击文件卡片弹出文本预览框 |
| 让模型生成 HTML/SVG | 代码块右上角有“预览”按钮，能运行 |
| 输入超长文本 | 自动转为 `.txt` 附件并提示确认 |
| 刷新/换设备 | 会话和消息从服务器恢复 |
| 后台“存储管理” | 能看到数据库/缓冲区/文本区用量和文件 |
| 暗黑模式 | 登录页、管理后台、用户页可切换 |

---

## 9. 常见问题（HTTP/NAT）

**Q1：外网打不开，内网正常**
检查端口转发、公网 IP、运营商是否封锁端口、是否 CGNAT。

**Q2：登录后立刻跳回登录页**
HTTP 下 `COOKIE_SECURE` 必须为 `false`；检查服务器时间。

**Q3：上传大文件失败**
单文件上限 32MB；缓冲区总上限默认 2GB，可在后台调大或清理旧文件。

**Q4：文本文件无法预览**
确认文件扩展名在文本白名单（txt/cpp/py/js/json/md 等）；旧附件如果本地和服务器都没有原件，只能重新上传。

**Q5：数据库满了**
后台“存储管理”删除不用的会话/文件，再点“释放数据库空间（VACUUM）”；或调大数据库上限。

**Q6：想换端口**
同时修改 `.env` 的 `PORT`、Windows 防火墙规则、路由器端口转发，并重启服务。
