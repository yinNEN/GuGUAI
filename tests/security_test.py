"""文件缓冲、存储配额与安全相关测试。

前置：服务运行在 18080，Mock 上游运行在 18081，数据库为空。
"""
from __future__ import annotations

import io
import sys

import httpx

BASE = "http://127.0.0.1:18080"
ADMIN_PASSWORD = "admin12345"
failures: list[str] = []


def check(condition: bool, name: str, detail: str = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        failures.append(name)
        print(f"FAIL  {name}  {detail}")


def login(client: httpx.Client, username: str, password: str) -> httpx.Response:
    return client.post("/api/auth/login", json={"username": username, "password": password})


def main() -> int:
    admin = httpx.Client(base_url=BASE, timeout=120, follow_redirects=False)
    anon = httpx.Client(base_url=BASE, timeout=30, follow_redirects=False)

    r = login(admin, "admin", ADMIN_PASSWORD)
    check(r.status_code == 200, "管理员登录")
    admin.put("/api/admin/apikey", json={"api_key": "sk-security-test-123456"})

    # ---- 上传与预览 ----
    r = admin.post(
        "/api/files",
        files={"file": ("../../evil.txt", b"<script>alert(1)</script>\nhello", "text/plain")},
        data={"purpose": "user_data"},
    )
    check(r.status_code == 200, "文本文件可上传", r.text)
    text_file = r.json()
    check(text_file["bucket"] == "text", "纯文本进入文本区", text_file.get("bucket"))
    check(text_file["name"] == "evil.txt", "文件名路径穿越被清理", text_file.get("name"))

    r = admin.get(f"/api/files/{text_file['id']}/text")
    check(r.status_code == 200 and "alert(1)" in r.json()["text"], "文本预览返回原文")
    check(r.headers.get("content-type", "").startswith("application/json"), "文本预览是 JSON 而非 HTML")

    r = admin.get(f"/api/files/{text_file['id']}")
    check(
        r.status_code == 200
        and "attachment" in r.headers.get("content-disposition", "")
        and r.headers.get("content-type") == "application/octet-stream",
        "下载强制 attachment，HTML/文本不会在本站执行",
        r.headers.get("content-disposition", ""),
    )
    check(r.headers.get("x-content-type-options") == "nosniff", "下载带 nosniff")

    r = admin.post(
        "/api/files",
        files={"file": ("image.png", b"\x89PNG\r\n\x1a\n" + b"x" * 64, "image/png")},
    )
    check(r.status_code == 200, "图片进入缓冲区", r.text)
    image_file = r.json()
    check(image_file["bucket"] == "buffer", "图片进入 buffer 区")
    r = admin.get(f"/api/files/{image_file['id']}?inline=1")
    check(
        r.status_code == 200
        and r.headers.get("content-type") == "image/png"
        and "inline" in r.headers.get("content-disposition", ""),
        "安全图片可内联预览",
    )

    r = admin.get(f"/api/files/{image_file['id']}/text")
    check(r.status_code == 400, "二进制文件不能按文本预览", str(r.status_code))

    # ---- 鉴权 ----
    r = anon.get(f"/api/files/{image_file['id']}")
    check(r.status_code == 401, "未登录不能下载文件", str(r.status_code))
    r = admin.head(f"/api/files/{image_file['id']}")
    check(r.status_code == 200, "HEAD 可检查自己的文件", str(r.status_code))

    r = admin.post("/api/admin/users", json={"username": "bob", "password": "bobpass123", "role": "user"})
    check(r.status_code == 200, "创建普通用户 bob", r.text)
    bob = httpx.Client(base_url=BASE, timeout=60, follow_redirects=False)
    check(login(bob, "bob", "bobpass123").status_code == 200, "bob 登录")

    r = bob.get(f"/api/files/{image_file['id']}")
    check(r.status_code == 404, "普通用户不能下载他人文件", str(r.status_code))
    r = bob.head(f"/api/files/{image_file['id']}")
    check(r.status_code == 404, "普通用户 HEAD 也看不到他人文件", str(r.status_code))
    r = bob.delete(f"/api/files/{image_file['id']}")
    check(r.json().get("ok") is False, "普通用户不能删除他人文件")
    r = bob.get("/api/admin/storage")
    check(r.status_code == 403, "普通用户不能查看存储管理", str(r.status_code))
    r = bob.put("/api/admin/storage", json={"dbMb": 1024})
    check(r.status_code == 403, "普通用户不能修改容量上限", str(r.status_code))

    # ---- 上传大小限制 ----
    big = io.BytesIO(b"x" * (33 * 1024 * 1024))
    r = admin.post("/api/files", files={"file": ("big.bin", big, "application/octet-stream")})
    check(r.status_code == 413, "超过 32MB 的上传被拒绝", str(r.status_code))

    # ---- CSRF ----
    r = admin.post(
        "/api/files",
        files={"file": ("csrf.txt", b"x", "text/plain")},
        headers={"Origin": "https://evil.example.com"},
    )
    check(r.status_code == 403, "跨站上传被拒绝", str(r.status_code))

    # ---- 管理端存储 ----
    r = admin.get("/api/admin/storage")
    check(r.status_code == 200 and r.json()["buffer"]["files"] >= 1, "存储统计可读", r.text)
    stats = r.json()
    check(stats["buffer"]["limit"] == 2 * 1024**3, "缓冲区默认 2GB")
    check(stats["db"]["limit"] == 4 * 1024**3, "数据库默认 4GB")

    r = admin.put("/api/admin/storage", json={"bufferMb": 64, "textMb": 64, "dbMb": 128})
    check(r.status_code == 200, "可调整容量上限", r.text)
    check(r.json()["buffer"]["limit"] == 64 * 1024**2, "缓冲区上限已生效")

    r = admin.post("/api/admin/storage/vacuum")
    check(r.status_code == 200 and "after" in r.json(), "VACUUM 可执行", r.text)

    r = admin.get("/api/admin/storage/files?bucket=text")
    check(r.status_code == 200 and any(f["id"] == text_file["id"] for f in r.json()["files"]), "后台可列出文本文件")

    r = admin.delete(f"/api/admin/storage/files/{text_file['id']}")
    check(r.status_code == 200 and r.json()["ok"], "后台可删除文件")
    r = admin.get(f"/api/files/{text_file['id']}/text")
    check(r.status_code == 404, "删除后文件不可访问")

    # ---- 会话清理 ----
    r = admin.get("/api/admin/conversations")
    if r.json()["conversations"]:
        conv_id = r.json()["conversations"][0]["id"]
        r = admin.delete(f"/api/admin/conversations/{conv_id}")
        check(r.status_code == 200 and r.json()["ok"], "后台可删除会话")
    else:
        check(True, "后台可删除会话（无会话，跳过）")

    # ---- 账号资料接口 ----
    r = admin.get("/api/auth/me")
    check(r.status_code == 200 and "created_at" in r.json() and "last_login_at" in r.json(), "用户资料接口含注册/登录时间")

    r = admin.get("/ui/account")
    check(r.status_code == 200 and "用户页面" in r.text, "用户页面可访问")

    # ---- 聊天引用缓冲文件：服务端应替换为 DeepSeek file_id ----
    r = admin.post("/api/files", files={"file": ("chat.txt", b"hello file", "text/plain")},
                   data={"purpose": "user_data"})
    chat_file = r.json()
    body = {
        "model": "deepseek-flash",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "看看附件"},
                {"type": "document", "source": {"type": "file", "file_id": chat_file["id"]}},
            ],
        }],
    }
    with admin.stream("POST", "/anthropic/v1/messages", json=body,
                      headers={"Accept": "text/event-stream"}, timeout=60) as resp:
        check(resp.status_code == 200, "聊天可引用服务器缓冲区文件", str(resp.status_code))
        for _ in resp.iter_bytes():
            pass

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for failure in failures:
            print(" -", failure)
        return 1
    print("全部安全/存储测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
