# Windows Server: NAT + HTTP Direct Public IP Access Deployment Guide

English | [简体中文](windows-server-nat-http.md)

This document addresses the following scenario:

- The service is deployed on a Windows Server on the internal network, for example `192.168.1.50:8000`;
- The public egress passes through router/firewall NAT;
- It is accessed externally at `http://<public-ip>:8000` directly;
- No domain name, no reverse proxy, and no HTTPS certificate.

> First complete the installation, `.env`, and administrator initialization steps in the main document [windows-server-deploy.en.md](windows-server-deploy.en.md), then perform the NAT and upgrade configuration described here.

---

## 1. Deployment topology

```
Public users
   │  http://203.0.113.10:8000
   ▼
Router / firewall (NAT port forwarding)
   │  203.0.113.10:8000 → 192.168.1.50:8000
   ▼
Windows Server 192.168.1.50
   │  uvicorn 0.0.0.0:8000
   ▼
DeepSeek API (server outbound 443)
```

The following must be ensured:

1. The server's internal IP is **fixed** (DHCP reservation or static IP);
2. Windows Firewall allows port 8000;
3. The router forwards public port 8000 to port 8000 on the server's internal address;
4. The server can reach `https://api.deepseek.com` outbound;
5. The public IP is not a carrier CGNAT address (see Section 4).

---

## 2. Windows Server configuration

### 2.1 Fix the internal IP

Set a static address under "Network Connections → Properties → IPv4", for example:

```
IP:      192.168.1.50
Mask:    255.255.255.0
Gateway: 192.168.1.1
DNS:     192.168.1.1 / 223.5.5.5
```

### 2.2 Allow traffic through the firewall

In an administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName "GuGuAI Chat 8000" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
```

If you only want to allow access from the internal network/a specific subnet, add `-RemoteAddress 192.168.1.0/24`, but note that after NAT forwarding the source address is usually the router's internal address, so adjust it to your actual network.

### 2.3 Configure `.env`

```ini
HOST=0.0.0.0
PORT=8000
COOKIE_SECURE=false
ADMIN_PASSWORD=change-me-to-a-strong-password
```

For direct HTTP access, `COOKIE_SECURE` must be `false`; otherwise the browser will not store the login cookie.

### 2.4 Start and verify

```powershell
cd D:\apps\deepseek-chat\server
.\start.bat
```

Access from the server itself:

```text
http://127.0.0.1:8000/healthz
http://192.168.1.50:8000/login
```

Access `http://192.168.1.50:8000/login` from another computer on the internal network, and only after confirming it opens should you set up port forwarding.

### 2.5 Register as a service (recommended)

```powershell
.\install-service-nssm.ps1 -NssmPath C:\tools\nssm\nssm.exe
```

Or use a scheduled task:

```powershell
.\install-startup-task.ps1
```

---

## 3. Router NAT port forwarding

The menu name varies between routers; it is usually found under:

- "Port Forwarding"
- "Virtual Server"
- "NAT / Port Mapping"
- "Firewall → Port Mapping"

Add a TCP rule:

| Item | Value |
| --- | --- |
| External port | 8000 (if the carrier blocks 80/443/8000, use 8080, 8443, etc.) |
| Internal IP | 192.168.1.50 |
| Internal port | 8000 |
| Protocol | TCP |

After saving, access it from the external network using mobile data:

```text
http://<public-ip>:8000/healthz
```

If it returns `{"ok":true,...}`, it is working.

Common problems:

- **Cannot open it**: confirm the public IP is correct, the rule protocol is TCP, Windows Firewall allows the port, and the service on the server is running;
- **Only the internal network can open it**: the router rule is not taking effect / NAT hairpin (loopback) is unsupported; test from the external network with mobile 4G/5G, not from the same internal Wi-Fi;
- **The port is blocked by the carrier**: switch to a port such as 8080/18000, and change `PORT` in `.env`, the firewall rule, and the port forwarding all at the same time;
- **The public IP changes**: use DDNS (Oray/PeanutHull, No-IP, Cloudflare DDNS, etc.) to bind a domain name, then access it through the domain name.

---

## 4. Determining whether you are behind CGNAT (important)

If the router's WAN IP differs from "my public IP", or the WAN IP is in `10.x`, `100.64.x`, `172.16~31.x`, or `192.168.x`, you are usually behind the carrier's large internal network (CGNAT), and **port forwarding will not work**.

Troubleshooting:

1. Check the WAN IP on the router status page;
2. On the server, visit `https://ipinfo.io/ip` or search for "IP" on a search engine to see the public IP;
3. If the two differ → CGNAT.

Available options under CGNAT (choose any one):

- **IPv6**: request a public IPv6 address from the carrier, allow port 8000 on the router, and use `http://[IPv6]:8000`;
- **FRP / nps**: use a cloud server with a public IP as a relay;
- **Cloudflare Tunnel**: free, comes with HTTPS, requires no public IP or port forwarding — recommended;
- **Tailscale / ZeroTier**: build a virtual internal network; only devices joined to the network can access it, giving the best security;
- Contact the carrier to request a public IP.

---

## 5. Security limitations of direct HTTP and how to mitigate them

Under HTTP, login passwords, session cookies, chat content, and uploaded files all travel across the public internet in plaintext and may be eavesdropped on by an attacker on the same network. At a minimum, do the following:

1. Administrators and users should use **independent strong passwords**, not reused from other websites;
2. Do not expose port 8000 to everyone; restrict source IPs wherever possible;
3. Regularly review the "Audit Log" in the admin console, and immediately change the password/disable the account if an abnormal login is detected;
4. Limit the number of users, and disable departed staff in a timely manner;
5. Back up `server\data` regularly;
6. Do not enter the API Key into the browser settings (in this version the Key exists only on the server side);
7. If conditions permit, prefer **Cloudflare Tunnel / Tailscale / FRP + HTTPS**, or obtain a domain name and use Caddy with automatic certificates.

> In this version all API Keys are on the server side and the browser only receives a placeholder value, so even if the HTTP traffic is eavesdropped on, the DeepSeek Key will not leak; however, login credentials and chat content should still be regarded as potentially exposed.

---

## 6. If you want to move to HTTPS later (optional)

You cannot obtain a trusted certificate for a bare `http://<public-ip>` (a certificate must be bound to a domain name). Options:

### 6.1 Domain name + Caddy (recommended)

1. Point the domain's A record to the public IP;
2. Forward ports 80/443 on the router to the server;
3. Install Caddy, `Caddyfile`:

```caddyfile
chat.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

4. Change `.env` to:

```ini
HOST=127.0.0.1
COOKIE_SECURE=true
```

5. Allow only ports 80/443 in the firewall, and no longer expose 8000 to the public internet.

### 6.2 When ports 80/443 cannot be opened

- Cloudflare Tunnel: onboard the domain to Cloudflare, run `cloudflared tunnel` on the local machine, and HTTPS is automatic;
- FRP + cloud server: the cloud server terminates TLS on port 443 and forwards traffic to the internal network;
- Tailscale/ZeroTier: not exposed to the public internet; members access it through the virtual internal network IP, and traffic is encrypted.

### 6.3 Self-signed certificates

You can issue a self-signed certificate for the IP, but the browser will show an "insecure" warning, and trust must be installed on every client; the maintenance cost is high, so this is not recommended for multi-user use.

---

## 7. Upgrading from an older version (HTTP/NAT scenario)

### 7.1 Back up before upgrading

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

### 7.2 Replace files

Leave the following untouched:

- `server\data\` (database, keys, file buffer, text files)
- `server\.env`

Overwrite:

- `web\`
- `server\app\`
- `server\pages\`
- `server\requirements.txt`

### 7.3 Install new dependencies

The new version adds `python-multipart` (file uploads), so you must run:

```powershell
cd D:\apps\deepseek-chat\server
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 7.4 Start and check

> On startup, the new version automatically creates the `session_files` table and backfills attachment references from existing session snapshots; no manual database migration is needed.

```powershell
Start-Service DeepSeekChat
# or .\start.bat
```

Access:

```text
http://127.0.0.1:8000/healthz
http://<public-ip>:8000/login
```

### 7.5 Storage notes for the new version

| Path | Contents | Default limit |
| --- | --- | --- |
| `server\data\chat.db` | Users, session snapshots, message projections, configuration | 4 GB (adjustable in the admin console) |
| `server\data\files\` | File buffer (images, PDFs, Word, etc.) | 2 GB (adjustable in the admin console; when exceeded, the least recently used files are deleted) |
| `server\data\text\` | Plain-text/code files | 1 GB (adjustable in the admin console, independent of the buffer) |

In the admin console → "Storage Management" you can:

- Adjust the limits for the database, buffer, and text area (in MB);
- View the file list and delete files;
- Delete conversations;
- Run `VACUUM` to reclaim database space.

Once the database reaches its limit, chat/sync returns "database space is full", and an administrator must delete unused conversations to free up space.

### 7.6 Ports/forwarding do not need to change

The new version does not change the listening port or paths, so the router NAT and Windows Firewall rules can stay as they are. If you have changed `PORT` in `.env`, remember to update the port forwarding accordingly.

---

## 8. Post-update acceptance checklist

| Check | Expected result |
| --- | --- |
| `http://<public-ip>:8000/healthz` | `{"ok":true}` |
| `/login` page | Shows GuGuAI, with a light/dark mode button in the top-right corner |
| Sending text after logging in | Normal streaming replies, and the conversation appears in the sidebar |
| Uploading an image/PDF | Upload succeeds, and a thumbnail/file card can be previewed in the message |
| Uploading txt/cpp | Clicking the file card pops up a text preview box |
| Asking the model to generate HTML/SVG | The code block has a "Preview" button in the top-right corner and can run |
| Entering very long text | Automatically converted to a `.txt` attachment with a confirmation prompt |
| Refresh/switching devices | Conversations and messages are restored from the server |
| "Storage Management" in the admin console | Database/buffer/text area usage and files are visible |
| Dark mode | Can be toggled on the login page, the admin console, and user pages |

---

## 9. FAQ (HTTP/NAT)

**Q1: It cannot be opened from the external network, but works on the internal network**
Check the port forwarding, the public IP, whether the carrier blocks the port, and whether you are behind CGNAT.

**Q2: After logging in, I am immediately sent back to the login page**
Under HTTP, `COOKIE_SECURE` must be `false`; also check the server time.

**Q3: Uploading a large file fails**
The per-file limit is 32 MB; the total buffer limit defaults to 2 GB and can be increased in the admin console, or old files can be cleaned up.

**Q4: A text file cannot be previewed**
Confirm that the file extension is on the text whitelist (txt/cpp/py/js/json/md, etc.); for old attachments, if no original exists both locally and on the server, the only option is to upload them again.

**Q5: The database is full**
In "Storage Management" in the admin console, delete unused conversations/files, then click "Release database space (VACUUM)"; or increase the database limit.

**Q6: I want to change the port**
Change `PORT` in `.env`, the Windows Firewall rule, and the router port forwarding at the same time, then restart the service.
