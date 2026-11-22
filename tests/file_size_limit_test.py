"""所有上传文件（文本/二进制）统一 32MB 上限测试。"""
from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:18080"
failures = []


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def main():
    c = httpx.Client(base_url=BASE, timeout=300)
    check(c.post("/api/auth/login", json={"username": "admin", "password": "admin12345"}).status_code == 200, "管理员登录")
    stats = c.get("/api/admin/storage").json()
    check(stats["fileMaxBytes"] == 32 * 1024 * 1024, "服务端单文件上限 32MB", str(stats["fileMaxBytes"]))

    over = 33 * 1024 * 1024
    under = 31 * 1024 * 1024
    for name, content, mime in [
        ("over.txt", b"a" * over, "text/plain"),
        ("over.bin", b"b" * over, "application/octet-stream"),
    ]:
        r = c.post("/api/files", files={"file": (name, content, mime)}, data={"purpose": "user_data"} if name.endswith(".txt") else None)
        check(r.status_code == 413, f"{name} 超过 32MB 被拒绝", f"{r.status_code} {r.text[:120]}")
        check("32MB" in r.text or "32 MB" in r.text, f"{name} 错误提示包含 32MB", r.text[:120])

    ids = []
    for name, content, mime, purpose in [
        ("ok.txt", b"c" * under, "text/plain", "user_data"),
        ("ok.bin", b"d" * under, "application/octet-stream", None),
    ]:
        r = c.post("/api/files", files={"file": (name, content, mime)}, data={"purpose": purpose} if purpose else None)
        check(r.status_code == 200, f"{name} 31MB 上传成功", f"{r.status_code} {r.text[:120]}")
        if r.status_code == 200:
            ids.append((r.json()["id"], r.json()["bucket"]))

    check(len(ids) == 2, "两个 31MB 文件均上传")
    buckets = {bucket for _, bucket in ids}
    check(buckets == {"text", "buffer"}, "文本进文本区、二进制进缓冲区", str(buckets))
    for file_id, _ in ids:
        check(c.head(f"/api/files/{file_id}").status_code == 200, f"文件 {file_id[:8]} 可访问")
        c.delete(f"/api/files/{file_id}")

    print()
    if failures:
        print("失败：", failures)
        return 1
    print("全部 32MB 上传上限测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
