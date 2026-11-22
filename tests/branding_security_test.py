"""自定义网页 API 的权限、校验与公开读取测试。"""
from __future__ import annotations

import pathlib
import sys

import httpx

BASE = "http://127.0.0.1:18080"
failures = []


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


def main():
    admin = httpx.Client(base_url=BASE, timeout=30)
    check(admin.post("/api/auth/login", json={"username": "admin", "password": "admin12345"}).status_code == 200, "管理员登录")

    r = admin.get("/api/public/branding")
    required = {"title", "subtitle", "mark", "color", "mark_mode", "mark_image", "has_icon"}
    check(r.status_code == 200 and required.issubset(set(r.json())), "公开品牌接口无需登录且字段完整", r.text)
    icon = pathlib.Path("tests/artifacts/dataset-image.webp").read_bytes()
    r = admin.post("/api/admin/branding/icon", files={"file": ("icon.webp", icon, "image/webp")})
    check(r.status_code == 200 and r.json()["has_icon"], "管理员可上传 1MB 内图片标识", r.text)
    r = admin.post("/api/admin/branding/icon", files={"file": ("fake.png", b"not image", "image/png")})
    check(r.status_code == 400, "伪造图片被拒绝", str(r.status_code))
    r = admin.post("/api/admin/branding/icon", files={"file": ("big.png", b"x" * (1024 * 1024 + 1), "image/png")})
    check(r.status_code == 400, "超过 1MB 图片被拒绝", str(r.status_code))

    r = admin.get("/api/admin/branding")
    check(r.status_code == 200, "管理员可读品牌设置", r.text)
    r = admin.put(
        "/api/admin/branding",
        json={"title": "安全品牌", "subtitle": "副标题", "mark": "S", "color": "#123456"},
    )
    check(r.status_code == 200 and r.json()["title"] == "安全品牌", "管理员可保存品牌设置", r.text)
    check(admin.get("/api/public/branding").json()["mark"] == "S", "公开接口立即反映修改")

    r = admin.put(
        "/api/admin/branding",
        json={"title": "x", "subtitle": "", "mark": "x", "color": "red"},
    )
    check(r.status_code == 422, "非法品牌色被拒绝", str(r.status_code))
    r = admin.put(
        "/api/admin/branding",
        json={"title": "x" * 81, "subtitle": "", "mark": "x", "color": "#123456"},
    )
    check(r.status_code == 422, "超长标题被拒绝", str(r.status_code))

    admin.post("/api/admin/users", json={"username": "brand_sec", "password": "brandsec123", "role": "user"})
    user = httpx.Client(base_url=BASE, timeout=30)
    check(user.post("/api/auth/login", json={"username": "brand_sec", "password": "brandsec123"}).status_code == 200, "普通用户登录")
    r = user.put(
        "/api/admin/branding",
        json={"title": "hacked", "subtitle": "", "mark": "H", "color": "#000000"},
    )
    check(r.status_code == 403, "普通用户不能修改品牌设置", str(r.status_code))
    r = user.post("/api/admin/branding/icon", files={"file": ("icon.webp", icon, "image/webp")})
    check(r.status_code == 403, "普通用户不能上传品牌图片", str(r.status_code))
    check(user.get("/api/admin/branding").status_code == 403, "普通用户不能读取管理端品牌设置")
    r = user.post(
        "/api/auth/change-password",
        json={"current_password": "brandsec123", "new_password": "brandsec456"},
    )
    check(r.status_code == 200, "测试用户可用于清理")

    # 恢复默认，避免影响其他测试
    admin.delete("/api/admin/branding/icon")
    admin.put(
        "/api/admin/branding",
        json={"title": "GuGuAI", "subtitle": "Powered by Deepseek.", "mark": "G", "color": "#4d6bfe", "mark_mode": "text"},
    )

    print()
    if failures:
        print("失败：", failures)
        return 1
    print("全部品牌设置安全测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
