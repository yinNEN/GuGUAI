"""会话删除/文件清理/账号漫游测试。"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:18080"
ADMIN_PASSWORD = "admin12345"
failures: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def login(client: httpx.Client, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def upload(client: httpx.Client, name: str, content: bytes, mime: str):
    return client.post("/api/files", files={"file": (name, content, mime)}, data={"purpose": "user_data"})


def session(sid: str, title: str, files: list[dict], stamp: int):
    return {
        "id": sid,
        "title": title,
        "createdAt": stamp,
        "updatedAt": stamp,
        "messages": [
            {
                "id": f"{sid}-u",
                "role": "user",
                "content": "hello",
                "files": files,
                "createdAt": stamp,
            }
        ],
    }


def file_meta(file_id: str, name: str, size: int) -> dict:
    return {
        "id": f"local-{file_id}",
        "serverId": file_id,
        "name": name,
        "size": size,
        "isImage": False,
        "mimeType": "text/plain",
    }


def main() -> int:
    admin = httpx.Client(base_url=BASE, timeout=60)
    check(login(admin, "admin", ADMIN_PASSWORD).status_code == 200, "管理员登录")
    admin.put("/api/admin/apikey", json={"api_key": "sk-cleanup-test-123456"})
    now = int(time.time() * 1000)

    # ---- 账号漫游：同一账号第二个客户端能读到会话和文件 ----
    r1 = upload(admin, "roam.txt", b"roam content", "text/plain")
    f1 = r1.json()["id"]
    s1 = session("cleanup-roam", "漫游会话", [file_meta(f1, "roam.txt", 12)], now)
    check(admin.put("/api/sync/sessions/cleanup-roam", json=s1).status_code == 200, "创建漫游会话")
    check(admin.head(f"/api/files/{f1}").status_code == 200, "文件在服务端")
    second = httpx.Client(base_url=BASE, timeout=60)
    check(login(second, "admin", ADMIN_PASSWORD).status_code == 200, "第二个客户端登录")
    check(second.get("/api/sync/sessions/cleanup-roam").status_code == 200, "第二个客户端读到会话")
    check(second.get(f"/api/files/{f1}/text").status_code == 200, "第二个客户端读到文本文件")
    check(second.get(f"/api/files/{f1}").status_code == 200, "第二个客户端下载文件")

    # ---- 删除会话：快照/投影/文件/引用都应清理 ----
    check(admin.delete("/api/sync/sessions/cleanup-roam").status_code == 200, "删除会话")
    check(admin.get("/api/sync/sessions/cleanup-roam").status_code == 404, "快照已删除")
    check(admin.head(f"/api/files/{f1}").status_code == 404, "文本文件已删除")
    stats = admin.get("/api/admin/storage").json()
    check(stats["text"]["used"] == 0 and stats["text"]["files"] == 0, "文本区已清理", str(stats["text"]))
    check(all(c["title"] != "漫游会话" for c in admin.get("/api/admin/conversations").json()["conversations"]), "投影会话已删除")

    # ---- 共享文件：删一个会话不能删文件 ----
    r2 = upload(admin, "shared.txt", b"shared", "text/plain")
    f2 = r2.json()["id"]
    shared = file_meta(f2, "shared.txt", 6)
    admin.put("/api/sync/sessions/shared-a", json=session("shared-a", "共享A", [shared], now + 10))
    admin.put("/api/sync/sessions/shared-b", json=session("shared-b", "共享B", [shared], now + 11))
    admin.delete("/api/sync/sessions/shared-a")
    check(admin.head(f"/api/files/{f2}").status_code == 200, "删除一个共享会话后文件保留")
    admin.delete("/api/sync/sessions/shared-b")
    check(admin.head(f"/api/files/{f2}").status_code == 404, "删除最后一个共享会话后文件删除")

    # ---- 整体替换：只保留新快照引用的文件 ----
    f3 = upload(admin, "keep.txt", b"keep", "text/plain").json()["id"]
    f4 = upload(admin, "drop.txt", b"drop", "text/plain").json()["id"]
    admin.put("/api/sync/sessions/keep-s", json=session("keep-s", "保留", [file_meta(f3, "keep.txt", 4)], now + 20))
    admin.put("/api/sync/sessions/drop-s", json=session("drop-s", "丢弃", [file_meta(f4, "drop.txt", 4)], now + 21))
    r = admin.post(
        "/api/sync/sessions/replace",
        json={"sessions": [session("keep-s", "保留", [file_meta(f3, "keep.txt", 4)], now + 20)]},
    )
    check(r.status_code == 200, "整体替换成功", r.text)
    check(admin.head(f"/api/files/{f3}").status_code == 200, "替换后保留文件存在")
    check(admin.head(f"/api/files/{f4}").status_code == 404, "替换后未引用文件删除")

    # ---- 后台删除会话也应清理文件 ----
    f5 = upload(admin, "admin-del.txt", b"admin delete", "text/plain").json()["id"]
    admin.put(
        "/api/sync/sessions/admin-del",
        json=session("admin-del", "后台删除测试", [file_meta(f5, "admin-del.txt", 12)], now + 30),
    )
    convs = admin.get("/api/admin/conversations").json()["conversations"]
    target = next(c for c in convs if c["title"] == "后台删除测试")
    check(admin.delete(f"/api/admin/conversations/{target['id']}").status_code == 200, "后台删除会话")
    check(admin.head(f"/api/files/{f5}").status_code == 404, "后台删除会话后文件清理")

    # ---- 普通用户不能删除他人会话/文件 ----
    admin.post("/api/admin/users", json={"username": "cleaner", "password": "cleaner123", "role": "user"})
    other = httpx.Client(base_url=BASE, timeout=60)
    check(login(other, "cleaner", "cleaner123").status_code == 200, "普通用户登录")
    f6 = upload(admin, "private.txt", b"private", "text/plain").json()["id"]
    check(other.head(f"/api/files/{f6}").status_code == 404, "普通用户看不到他人文件")
    check(other.delete("/api/files/{f6}").json()["ok"] is False, "普通用户不能删除他人文件")

    # ---- 保留会话的文件仍存在，清理后无残留 ----
    check(admin.head(f"/api/files/{f3}").status_code == 200, "仍被会话引用的文件保留")
    admin.delete("/api/sync/sessions/keep-s")
    check(admin.head(f"/api/files/{f3}").status_code == 404, "删除最后引用会话后文件清理")
    admin.delete(f"/api/files/{f6}")
    stats = admin.get("/api/admin/storage").json()
    check(stats["buffer"]["files"] == 0, "缓冲区无残留文件", str(stats["buffer"]))
    check(stats["text"]["files"] == 0, "文本区无残留文件", str(stats["text"]))

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for failure in failures:
            print(" -", failure)
        return 1
    print("全部清理/漫游测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
