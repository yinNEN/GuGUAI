from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .common import client_ip, cookie_settings, require_user
from .config import settings
from .security import (
    hash_password,
    login_limiter,
    new_session_token,
    token_hash,
    verify_password,
)
from .store import (
    audit,
    create_session,
    delete_session,
    delete_user_sessions,
    get_user,
    get_user_by_username,
    touch_login,
    update_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 用于在用户不存在时也执行一次哈希校验，避免通过响应时间枚举用户名
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "must_change_password": bool(user.get("must_change_password")),
    }


@router.post("/login")
async def login(payload: LoginIn, request: Request):
    ip = client_ip(request)
    wait = login_limiter.check(ip)
    if wait:
        return JSONResponse(
            {"detail": f"登录尝试过于频繁，请 {wait} 秒后再试"},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    username = payload.username.strip()
    user = get_user_by_username(username)
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
    ok = bool(user and user["is_active"] and verify_password(payload.password, user["password_hash"]))
    if not ok:
        login_limiter.record_failure(ip)
        audit(user["id"] if user else None, "login_failed", f"username={username}", ip)
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)

    login_limiter.reset(ip)
    token = new_session_token()
    create_session(
        token_hash(token),
        user["id"],
        settings.session_ttl_hours,
        ip,
        request.headers.get("user-agent", ""),
    )
    touch_login(user["id"])
    audit(user["id"], "login_success", "", ip)

    response = JSONResponse({"ok": True, "user": public_user(user)})
    response.set_cookie(value=token, **cookie_settings(request))
    return response


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(settings.cookie_name)
    if token:
        delete_session(token_hash(token))
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings.cookie_name, path="/")
    return response


@router.get("/me")
async def me(request: Request):
    user = require_user(request)
    full = get_user(user["id"]) or user
    return {
        **public_user(user),
        "created_at": full.get("created_at"),
        "last_login_at": full.get("last_login_at"),
    }


@router.post("/change-password")
async def change_password(payload: ChangePasswordIn, request: Request):
    user = require_user(request)
    full = get_user_by_username(user["username"])
    if not full or not verify_password(payload.current_password, full["password_hash"]):
        return JSONResponse({"detail": "当前密码不正确"}, status_code=400)
    if payload.current_password == payload.new_password:
        return JSONResponse({"detail": "新密码不能与当前密码相同"}, status_code=400)
    if len(payload.new_password.encode("utf-8")) > 72:
        return JSONResponse({"detail": "密码过长（UTF-8 编码后不能超过 72 字节）"}, status_code=400)

    update_user(full["id"], password_hash=hash_password(payload.new_password), must_change_password=0)
    delete_user_sessions(full["id"])

    token = new_session_token()
    create_session(
        token_hash(token),
        full["id"],
        settings.session_ttl_hours,
        client_ip(request),
        request.headers.get("user-agent", ""),
    )
    audit(full["id"], "change_password", "", client_ip(request))
    response = JSONResponse({"ok": True})
    response.set_cookie(value=token, **cookie_settings(request))
    return response
