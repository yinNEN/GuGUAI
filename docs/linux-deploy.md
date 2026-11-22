# Linux deployment

[简体中文](linux-deploy.zh-CN.md) | English

> **Recommendation:** Windows Server is the primary supported deployment
> target. The author only has a Windows environment for debugging, so Linux
> instructions and scripts are provided as a convenience and are not
> continuously tested. See [windows-server-deploy.md](windows-server-deploy.md)
> for the recommended path.

The backend and these scripts are licensed under Apache-2.0. The frontend in
`chat_demo_score/chat-demo` remains under the upstream MIT license; see
`LICENSE`, `NOTICE`, and `THIRD_PARTY_NOTICES.md`.

## 1. Requirements

- Linux with systemd (Ubuntu 22.04/24.04, Debian 12, Rocky/Alma 9, etc.)
- Python 3.12 recommended (3.10+ minimum)
- `python3-venv` and `python3-pip`
- Optional: nginx or Caddy for HTTPS/reverse proxy

Ubuntu/Debian example:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

## 2. Install

Copy the project to `/opt/deepseek-chat` (or another directory), then:

```bash
cd /opt/deepseek-chat/server
chmod +x install.sh start.sh
./install.sh
```

The installer creates `.venv`, installs `requirements.txt`, and copies
`.env.example` to `.env` if needed.

Edit at least:

```ini
HOST=127.0.0.1
PORT=8000
ADMIN_PASSWORD=replace-with-a-strong-password
COOKIE_SECURE=false   # true when served through HTTPS
```

`DATA_DIR=./data` and `WEB_DIR=../web` are relative to `server/`.

## 3. Test manually

```bash
cd /opt/deepseek-chat/server
./start.sh
```

Open `http://server-ip:8000`, log in as the admin, set the API Key, and create
users. Press Ctrl+C to stop.

## 4. Run as a systemd service

Create a dedicated user and set ownership:

```bash
sudo useradd --system --home /opt/deepseek-chat --shell /usr/sbin/nologin deepseek
sudo chown -R deepseek:deepseek /opt/deepseek-chat
```

Copy and adjust the provided unit:

```bash
sudo cp /opt/deepseek-chat/server/deepseek-chat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-chat
sudo systemctl status deepseek-chat
```

Logs:

```bash
journalctl -u deepseek-chat -f
# application log
tail -f /opt/deepseek-chat/server/data/server.log
```

## 5. Reverse proxy / HTTPS

Do not expose the Python port directly to the public Internet if possible.
Point nginx at `127.0.0.1:8000`, terminate TLS, and set:

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

Example nginx site:

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

`proxy_buffering off` is important for SSE chat streaming.

## 6. Upgrade

```bash
sudo systemctl stop deepseek-chat
cd /opt/deepseek-chat
# backup server/data first
cp -a server/data "server/data.backup.$(date +%Y%m%d-%H%M%S)"
# replace web/, server/app/, server/pages/, server/*.sh as needed
cd server
./install.sh
sudo systemctl start deepseek-chat
```

Keep `server/data/` and `server/.env`.

## 7. Notes

- Use `COOKIE_SECURE=true` only with HTTPS; otherwise login cookies will not be
  sent over plain HTTP.
- Back up `server/data/chat.db` and `server/data/secret.key` together.
- If no admin exists, the next start restores `admin` from `ADMIN_PASSWORD`, or
  generates a random password in `server/data/initial-admin-password.txt`.
- Firewall: allow only 443 (and 80 for redirect) publicly; keep 8000 local.
