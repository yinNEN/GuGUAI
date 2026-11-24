from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import admin_api, auth_api, branding, chat_api, files_api, my_api, pages, sync_api
from . import files as file_store
from .config import settings
from .db import db, init_db
from .security import hash_password, token_hash
from .store import (
    audit,
    backfill_session_files,
    create_user,
    delete_user_sessions,
    get_api_key,
    get_session_user,
    get_user_by_username,
    prune_audit_logs,
    purge_expired_sessions,
    update_user,
)
from .upstream import api_error, close_client

logger = logging.getLogger("deepseek.server")

PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/public/branding", "/api/public/branding/icon", "/healthz", "/favicon.ico", "/robots.txt"}
JSON_PREFIXES = ("/api/", "/models", "/anthropic/", "/v1/tools/")


def ensure_bootstrap_admin() -> None:
    """保证只有一个管理员；没有管理员时按 .env/随机密码恢复 admin 账号。"""
    with db() as conn:
        admins = [
            dict(row)
            for row in conn.execute(
                "SELECT id, username, role, is_active FROM users WHERE role = 'admin' ORDER BY id"
            ).fetchall()
        ]
    if admins:
        keep = next((item for item in admins if item["username"] == settings.admin_username), None)
        keep = keep or next((item for item in admins if item["is_active"]), admins[0])
        for extra in admins:
            if extra["id"] != keep["id"]:
                update_user(extra["id"], role="user")
                audit(None, "admin_demote_extra", extra["username"])
                logger.warning("只允许一个管理员，已将多余管理员 %s 降级为普通用户", extra["username"])
        if not keep["is_active"]:
            update_user(keep["id"], is_active=1)
        return

    if settings.admin_password and len(settings.admin_password.encode("utf-8")) > 72:
        raise RuntimeError("ADMIN_PASSWORD 过长：UTF-8 编码后不能超过 72 字节（bcrypt 限制），请修改 .env")

    password = settings.admin_password or secrets.token_urlsafe(9)
    password_hash = hash_password(password)
    existing = get_user_by_username(settings.admin_username)
    if existing:
        update_user(
            existing["id"],
            role="admin",
            is_active=1,
            password_hash=password_hash,
            must_change_password=0,
        )
        delete_user_sessions(existing["id"])
        audit(None, "bootstrap_admin_restore", settings.admin_username)
        logger.warning("未发现管理员，已按兜底策略恢复账号 %s", settings.admin_username)
    else:
        create_user(settings.admin_username, password_hash, role="admin")
        audit(None, "bootstrap_admin", settings.admin_username)
        logger.warning("已创建初始管理员：%s", settings.admin_username)

    if settings.admin_password:
        logger.warning("管理员密码来自环境变量 ADMIN_PASSWORD，请尽快修改")
    else:
        password_file = settings.data_dir / "initial-admin-password.txt"
        password_file.write_text(
            f"username: {settings.admin_username}\npassword: {password}\n", encoding="utf-8"
        )
        logger.warning(
            "已生成管理员 %s，随机密码：%s（已写入 %s），登录后请立即修改",
            settings.admin_username,
            password,
            password_file,
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.files_dir.mkdir(parents=True, exist_ok=True)
    settings.text_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    backfill_session_files()
    purge_expired_sessions()
    prune_audit_logs()
    file_store.purge_orphan_files()
    ensure_bootstrap_admin()
    api_key, source = get_api_key()
    logger.info("上游地址：%s", settings.upstream_base)
    logger.info("API Key 状态：%s", f"已配置（{source}）" if api_key else "未配置，请登录管理后台设置")
    if not (settings.web_dir / "index.html").exists():
        logger.error("未找到前端文件：%s", settings.web_dir / "index.html")
    cleanup_task = asyncio.create_task(_orphan_cleanup_loop())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await close_client()


async def _orphan_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(file_store.purge_orphan_files)
        except Exception:
            logger.warning("orphan file cleanup failed", exc_info=True)


def _origin_allowed(request: Request, origin: str) -> bool:
    if origin in settings.allowed_origins:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        return False
    return urlparse(origin).netloc.lower() == host.lower()


def _wants_json(path: str) -> bool:
    return path.startswith(JSON_PREFIXES)


def create_app() -> FastAPI:
    app = FastAPI(
        title="DeepSeek Chat Server",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path

        # 简单的 CSRF 防护：写操作必须同源（SameSite=Lax 之外的二次校验）
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and not _origin_allowed(request, origin):
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)

        token = request.cookies.get(settings.cookie_name)
        user = get_session_user(token_hash(token)) if token else None
        request.state.user = user

        # A tab opened under another account can keep using a shared browser
        # cookie after the user switches accounts in a second tab. Refuse writes
        # and reads from that stale tab before it can push one user's chats into
        # the other account. New clients send their expected user ID; older
        # clients without the header remain compatible.
        expected_user = request.headers.get("x-chat-user")
        if user is not None and expected_user and expected_user != str(user["id"]):
            return JSONResponse(
                {"detail": "登录账号已切换，请刷新页面后继续"},
                status_code=409,
                headers={"X-Session-User-Changed": "1"},
            )

        if path not in PUBLIC_PATHS and not path.startswith("/static/") and not path.startswith("/ui/assets/"):
            if user is None:
                if _wants_json(path):
                    response = api_error(401, "未登录或会话已过期，请重新登录", "authentication_error")
                    response.headers["X-Session-Expired"] = "1"
                    return response
                return RedirectResponse(f"/login?next={quote(path, safe='')}", status_code=302)
            if user.get("must_change_password"):
                # 被管理员标记为“下次登录必须修改密码”的账号，在改密前只能访问改密相关页面/接口
                allowed = (
                    path == "/ui/account"
                    or path == "/api/auth/me"
                    or path == "/api/auth/change-password"
                    or path == "/api/auth/logout"
                    or path == "/logout"
                )
                if not allowed:
                    if path.startswith("/api/") or _wants_json(path):
                        return JSONResponse(
                            {
                                "detail": "请先修改初始密码",
                                "must_change_password": True,
                            },
                            status_code=403,
                        )
                    return RedirectResponse("/ui/account?force=1", status_code=302)
            if path == "/admin" or path.startswith("/admin/") or path.startswith("/api/admin"):
                if user["role"] != "admin":
                    if path.startswith("/api/"):
                        return JSONResponse({"detail": "需要管理员权限"}, status_code=403)
                    return RedirectResponse("/ui/history", status_code=302)

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    app.include_router(pages.router)
    app.include_router(branding.router)
    app.include_router(auth_api.router)
    app.include_router(admin_api.router)
    app.include_router(chat_api.router)
    app.include_router(my_api.router)
    app.include_router(sync_api.router)
    app.include_router(files_api.router)

    static_dir = settings.web_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    assets_dir = settings.pages_dir / "assets"
    if assets_dir.exists():
        app.mount("/ui/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")

    return app


app = create_app()
