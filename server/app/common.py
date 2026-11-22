from __future__ import annotations

from fastapi import HTTPException, Request

from .config import settings


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


def current_user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def is_secure_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def cookie_settings(request: Request) -> dict:
    return {
        "key": settings.cookie_name,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure or is_secure_request(request),
        "path": "/",
        "max_age": settings.session_ttl_hours * 3600,
    }
