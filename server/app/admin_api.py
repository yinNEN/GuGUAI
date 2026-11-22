from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .common import client_ip, require_admin
from .branding import (
    ICON_MAX_BYTES,
    delete_icon,
    get_branding,
    save_branding,
    save_icon,
)
from . import files as file_store
from .config import settings
from .security import hash_password, mask_secret
from .store import (
    API_KEY_SETTING,
    audit,
    count_admins,
    create_user,
    delete_setting,
    delete_user,
    delete_user_sessions,
    get_api_key,
    get_conversation,
    get_user,
    get_user_by_username,
    list_audit,
    list_conversations,
    list_users,
    set_api_key,
    stats,
    update_user,
)
from .upstream import verify_api_key

router = APIRouter(prefix="/api/admin", tags=["admin"])

USERNAME_RE = re.compile(r"^[\w.\-]{2,64}$", re.UNICODE)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    must_change_password: bool = False


class UserUpdateIn(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=64)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    must_change_password: bool | None = None


class BrandingIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default="", max_length=120)
    mark: str = Field(default="", max_length=4)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    mark_mode: Literal["text", "image", "none"] = "text"


class ApiKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


class ApiKeyTestIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=512)


def _password_ok(password: str) -> bool:
    return len(password.encode("utf-8")) <= 72


def _key_info() -> dict:
    key, source = get_api_key()
    return {
        "configured": bool(key),
        "source": source,
        "masked": mask_secret(key) if key else "",
    }


@router.get("/branding")
async def branding(request: Request):
    require_admin(request)
    return get_branding()


@router.put("/branding")
async def update_branding(payload: BrandingIn, request: Request):
    admin = require_admin(request)
    try:
        result = save_branding(
            payload.title, payload.subtitle, payload.mark, payload.color, payload.mark_mode
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    audit(admin["id"], "branding_update", f"title={result['title']}", client_ip(request))
    return result


@router.post("/branding/icon")
async def upload_branding_icon(file: UploadFile, request: Request):
    admin = require_admin(request)
    if not file.filename:
        return JSONResponse({"detail": "请选择图片"}, status_code=400)
    data = await file.read(ICON_MAX_BYTES + 1)
    try:
        result = save_icon(data)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    audit(admin["id"], "branding_icon_upload", f"{len(data)} bytes", client_ip(request))
    return result


@router.delete("/branding/icon")
async def remove_branding_icon(request: Request):
    admin = require_admin(request)
    result = delete_icon()
    audit(admin["id"], "branding_icon_delete", "", client_ip(request))
    return result


@router.get("/overview")
async def overview(request: Request):
    require_admin(request)
    return {
        "stats": stats(),
        "api_key": _key_info(),
        "upstream_base": settings.upstream_base,
        "session_ttl_hours": settings.session_ttl_hours,
        "web_dir": str(settings.web_dir),
    }


# --------------------------------------------------------------------------
# 用户管理
# --------------------------------------------------------------------------
@router.get("/users")
async def users(request: Request):
    require_admin(request)
    return {"users": list_users()}


@router.post("/users")
async def create_user_api(payload: UserCreateIn, request: Request):
    admin = require_admin(request)
    username = payload.username.strip()
    if not USERNAME_RE.match(username):
        return JSONResponse({"detail": "用户名只能包含字母、数字、下划线、点、短横线（2-64 位）"}, status_code=400)
    if get_user_by_username(username):
        return JSONResponse({"detail": "用户名已存在"}, status_code=409)
    if not _password_ok(payload.password):
        return JSONResponse({"detail": "密码过长（UTF-8 编码后不能超过 72 字节）"}, status_code=400)
    user_id = create_user(
        username,
        hash_password(payload.password),
        role="user",
        must_change_password=1 if payload.must_change_password else 0,
    )
    audit(admin["id"], "user_create", f"username={username}", client_ip(request))
    return {"ok": True, "id": user_id}


@router.patch("/users/{user_id}")
async def update_user_api(user_id: int, payload: UserUpdateIn, request: Request):
    admin = require_admin(request)
    target = get_user(user_id)
    if not target:
        return JSONResponse({"detail": "用户不存在"}, status_code=404)

    disabling = payload.is_active is False and target["is_active"]
    if disabling and target["id"] == admin["id"]:
        return JSONResponse({"detail": "不能禁用当前登录的管理员账号"}, status_code=400)
    if disabling and target["role"] == "admin" and count_admins(active_only=False) <= 1:
        return JSONResponse({"detail": "系统至少需要保留一个管理员"}, status_code=400)

    updates: dict = {}
    if payload.username is not None:
        username = payload.username.strip()
        if target["id"] != admin["id"]:
            return JSONResponse({"detail": "管理员只能修改自己的用户名"}, status_code=400)
        if not USERNAME_RE.match(username):
            return JSONResponse({"detail": "用户名只能包含字母、数字、下划线、点、短横线（2-64 位）"}, status_code=400)
        occupied = get_user_by_username(username)
        if occupied and occupied["id"] != target["id"]:
            return JSONResponse({"detail": "用户名已存在"}, status_code=409)
        if username != target["username"]:
            updates["username"] = username
    if payload.is_active is not None:
        updates["is_active"] = 1 if payload.is_active else 0
    if payload.password:
        if not _password_ok(payload.password):
            return JSONResponse({"detail": "密码过长（UTF-8 编码后不能超过 72 字节）"}, status_code=400)
        updates["password_hash"] = hash_password(payload.password)
        updates["must_change_password"] = 1 if payload.must_change_password else 0
    elif payload.must_change_password is not None:
        updates["must_change_password"] = 1 if payload.must_change_password else 0

    update_user(user_id, **updates)
    if payload.password or payload.is_active is False:
        delete_user_sessions(user_id)

    audit(
        admin["id"],
        "user_update",
        f"user_id={user_id} fields={sorted(updates.keys())}",
        client_ip(request),
    )
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user_api(user_id: int, request: Request):
    admin = require_admin(request)
    target = get_user(user_id)
    if not target:
        return JSONResponse({"detail": "用户不存在"}, status_code=404)
    if target["id"] == admin["id"]:
        return JSONResponse({"detail": "不能删除当前登录的账号"}, status_code=400)
    if target["role"] == "admin" and count_admins(active_only=False) <= 1:
        return JSONResponse({"detail": "系统至少需要保留一个管理员"}, status_code=400)

    delete_user_sessions(user_id)
    file_store.delete_user_files(user_id)
    delete_user(user_id)
    audit(admin["id"], "user_delete", f"username={target['username']}", client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------------------
# API Key 管理（仅管理员可见，普通用户无任何接口可读取）
# --------------------------------------------------------------------------
@router.get("/apikey")
async def get_api_key_api(request: Request):
    require_admin(request)
    return _key_info()


@router.put("/apikey")
async def set_api_key_api(payload: ApiKeyIn, request: Request):
    admin = require_admin(request)
    key = payload.api_key.strip()
    if not key:
        return JSONResponse({"detail": "API Key 不能为空"}, status_code=400)
    set_api_key(key)
    audit(admin["id"], "apikey_update", mask_secret(key), client_ip(request))
    return {"ok": True, **_key_info()}


@router.delete("/apikey")
async def delete_api_key_api(request: Request):
    admin = require_admin(request)
    delete_setting(API_KEY_SETTING)
    audit(admin["id"], "apikey_delete", "", client_ip(request))
    return {"ok": True, **_key_info()}


@router.post("/apikey/test")
async def test_api_key_api(payload: ApiKeyTestIn, request: Request):
    require_admin(request)
    key = (payload.api_key or "").strip()
    if not key:
        key, _ = get_api_key()
    if not key:
        return JSONResponse({"ok": False, "detail": "尚未配置 API Key"}, status_code=400)
    ok, detail = await verify_api_key(key)
    return {"ok": ok, "detail": detail}


# --------------------------------------------------------------------------
# 会话记录
# --------------------------------------------------------------------------
def _conversation_detail(conv: dict) -> dict:
    messages = []
    for msg in conv["messages"]:
        try:
            content = json.loads(msg["content"])
        except Exception:
            content = msg["content"]
        messages.append({**msg, "content": content})
    return {**conv, "messages": messages}


@router.get("/conversations")
async def conversations(
    request: Request,
    user_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    require_admin(request)
    rows, total = list_conversations(user_id, limit=limit, offset=offset)
    return {"conversations": rows, "total": total}


@router.get("/conversations/{conv_id}")
async def conversation_detail(conv_id: int, request: Request):
    require_admin(request)
    conv = get_conversation(conv_id)
    if not conv:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return _conversation_detail(conv)


@router.get("/audit")
async def audit_logs(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    require_admin(request)
    return {"logs": list_audit(limit)}
