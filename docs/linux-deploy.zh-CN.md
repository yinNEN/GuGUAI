[English](linux-deploy.md) | 简体中文

# Linux 部署

> **建议：** Windows Server 是主要支持的部署目标。作者只有 Windows 环境可供调试，因此这里提供的 Linux 说明和脚本仅为方便使用者，并未经过持续测试。推荐路线请参阅 [windows-server-deploy.md](windows-server-deploy.md)。

后端及这些脚本采用 Apache-2.0 许可。`chat_demo_score/chat-demo` 中的前端仍遵循上游 MIT 许可；参见 `LICENSE`、`NOTICE` 和 `THIRD_PARTY_NOTICES.md`。

## 1. 环境要求

- 支持 systemd 的 Linux（Ubuntu 22.04/24.04、Debian 12、Rocky/Alma 9 等）
- 推荐 Python 3.12（最低 3.10+）
- `python3-venv` 和 `python3-pip`
- 可选：nginx 或 Caddy，用于 HTTPS/反向代理

Ubuntu/Debian 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

## 2. 安装

将项目复制到 `/opt/deepseek-chat`（或其他目录），然后执行：

```bash
cd /opt/deepseek-chat/server
chmod +x install.sh start.sh
./install.sh
```

安装脚本会创建 `.venv`、安装 `requirements.txt`，并在需要时将 `.env.example` 复制为 `.env`。

至少需要编辑以下配置：

```ini
HOST=127.0.0.1
PORT=8000
ADMIN_PASSWORD=replace-with-a-strong-password
COOKIE_SECURE=false   # 通过 HTTPS 提供服务时设为 true
```

`DATA_DIR=./data` 和 `WEB_DIR=../web` 均相对于 `server/`。

## 3. 手动测试

```bash
cd /opt/deepseek-chat/server
./start.sh
```

打开 `http://server-ip:8000`，以管理员身份登录，设置 API Key，并创建用户。按 Ctrl+C 停止。

## 4. 以 systemd 服务运行

创建专用用户并设置文件归属：

```bash
sudo useradd --system --home /opt/deepseek-chat --shell /usr/sbin/nologin deepseek
sudo chown -R deepseek:deepseek /opt/deepseek-chat
```

复制并调整随附的 unit 文件：

```bash
sudo cp /opt/deepseek-chat/server/deepseek-chat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-chat
sudo systemctl status deepseek-chat
```

日志：

```bash
journalctl -u deepseek-chat -f
# 应用日志
tail -f /opt/deepseek-chat/server/data/server.log
```

## 5. 反向代理 / HTTPS

如有可能，不要把 Python 端口直接暴露到公网。让 nginx 指向 `127.0.0.1:8000`，终止 TLS，并设置：

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

nginx 站点示例：

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate     /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;

    client_max_body_size 100m;

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

`proxy_buffering off` 对 SSE 聊天流式传输很重要。

## 6. 升级

```bash
sudo systemctl stop deepseek-chat
cd /opt/deepseek-chat
# 先备份 server/data
cp -a server/data "server/data.backup.$(date +%Y%m%d-%H%M%S)"
# 按需替换 web/、server/app/、server/pages/、server/*.sh
cd server
./install.sh
sudo systemctl start deepseek-chat
```

请保留 `server/data/` 和 `server/.env`。

## 7. 注意事项

- 仅在 HTTPS 环境下使用 `COOKIE_SECURE=true`；否则登录 Cookie 将无法通过明文 HTTP 发送。
- 将 `server/data/chat.db` 与 `server/data/secret.key` 一并备份。
- 若不存在管理员，下次启动时会依据 `ADMIN_PASSWORD` 恢复 `admin`，或在 `server/data/initial-admin-password.txt` 中生成一个随机密码。
- 防火墙：公网只放行 443（以及用于重定向的 80）；8000 端口保持在本地。
