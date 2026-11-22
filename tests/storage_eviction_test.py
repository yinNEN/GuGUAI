"""文本区与缓冲区超限后的最旧文件淘汰测试。"""
from __future__ import annotations

import sys
import time

import httpx

BASE = "http://127.0.0.1:18080"
failures = []


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def main():
    c = httpx.Client(base_url=BASE, timeout=180)
    check(c.post("/api/auth/login", json={"username": "admin", "password": "admin12345"}).status_code == 200, "管理员登录")
    check(c.put("/api/admin/storage", json={"bufferMb": 64, "textMb": 64, "dbMb": 128}).status_code == 200, "设置 64MB 限制")
    mb25 = 25 * 1024 * 1024

    # 文本区：3 个 25MB 文件，超出 64MB 后应删除最旧
    text_ids = []
    for i in range(3):
        content = (f"chunk-{i}-".encode() * (mb25 // 8 + 1))[:mb25]
        r = c.post("/api/files", files={"file": (f"evict-{i}.txt", content, "text/plain")}, data={"purpose": "user_data"})
        check(r.status_code == 200, f"文本文件 {i} 上传成功", f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            text_ids.append(r.json()["id"])
        time.sleep(0.05)
    check(len(text_ids) == 3, "三个文本文件均已上传")
    if len(text_ids) == 3:
        check(c.head(f"/api/files/{text_ids[0]}").status_code == 404, "文本区最旧文件已淘汰")
        check(c.head(f"/api/files/{text_ids[1]}").status_code == 200, "文本区较新文件保留")
        check(c.head(f"/api/files/{text_ids[2]}").status_code == 200, "文本区最新文件保留")
        st = c.get("/api/admin/storage").json()
        check(st["text"]["files"] == 2, "文本区文件数=2", str(st["text"]))
        check(st["text"]["used"] <= 64 * 1024**2, "文本区未超过上限", str(st["text"]))

    # 缓冲区：同样 3 个 25MB 非文本文件
    buf_ids = []
    for i in range(3):
        content = (f"buf-{i}-".encode() * (mb25 // 6 + 1))[:mb25]
        r = c.post("/api/files", files={"file": (f"evict-{i}.bin", content, "application/octet-stream")})
        check(r.status_code == 200, f"缓冲文件 {i} 上传成功", f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            buf_ids.append(r.json()["id"])
        time.sleep(0.05)
    if len(buf_ids) == 3:
        check(c.head(f"/api/files/{buf_ids[0]}").status_code == 404, "缓冲区最旧文件已淘汰")
        check(c.head(f"/api/files/{buf_ids[1]}").status_code == 200, "缓冲区较新文件保留")
        check(c.head(f"/api/files/{buf_ids[2]}").status_code == 200, "缓冲区最新文件保留")
        st = c.get("/api/admin/storage").json()
        check(st["buffer"]["files"] == 2, "缓冲区文件数=2", str(st["buffer"]))
        check(st["buffer"]["used"] <= 64 * 1024**2, "缓冲区未超过上限", str(st["buffer"]))

    print()
    if failures:
        print("失败：", failures)
        return 1
    print("全部淘汰策略测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
