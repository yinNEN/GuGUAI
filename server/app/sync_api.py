from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .common import client_ip, require_user
from . import files as file_store
from .store import (
    audit,
    delete_snapshot,
    get_snapshot,
    list_snapshots,
    rename_snapshot,
    replace_snapshots,
    upsert_snapshot,
)

router = APIRouter(prefix="/api/sync", tags=["sync"])

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MAX_SESSIONS = 500
MAX_MESSAGES = 5000
MAX_SESSION_BYTES = 8 * 1024 * 1024
MAX_REPLACE_BYTES = 40 * 1024 * 1024


class RenameIn(BaseModel):
    title: str = Field(max_length=200)


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=400)


def _normalize_session(payload: object, client_id: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("会话格式不正确")
    if str(payload.get("id") or "") != client_id:
        raise ValueError("会话 ID 与路径不一致")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("messages 必须是数组")
    if len(messages) > MAX_MESSAGES:
        raise ValueError("消息数量超出限制")

    now_ms = int(time.time() * 1000)
    created = payload.get("createdAt")
    updated = payload.get("updatedAt")
    if not isinstance(created, (int, float)) or created <= 0:
        created = now_ms
    if not isinstance(updated, (int, float)) or updated <= 0:
        updated = created
    title = payload.get("title")
    if not isinstance(title, str):
        title = ""

    normalized = {
        "id": client_id,
        "title": title[:200],
        "createdAt": int(created),
        "updatedAt": int(updated),
        "messages": [],
    }
    if "rootMessageId" in payload:
        normalized["rootMessageId"] = payload["rootMessageId"]

    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("消息格式不正确")
        role = message.get("role")
        if role not in ("user", "assistant"):
            raise ValueError("消息角色不合法")
        normalized["messages"].append(message)

    try:
        size = len(json.dumps(normalized, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ValueError("会话内容无法序列化") from None
    if size > MAX_SESSION_BYTES:
        raise ValueError("单个会话过大")
    return normalized


def _is_settled(session: dict) -> bool:
    for message in session.get("messages", []):
        if isinstance(message, dict) and message.get("role") == "assistant":
            if message.get("status") in ("pending", "streaming"):
                return False
    return True


def _content_length_ok(request: Request, limit: int) -> bool:
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    return length <= limit


@router.get("/sessions")
async def sync_list(request: Request):
    user = require_user(request)
    return {"sessions": list_snapshots(user["id"])}


@router.get("/sessions/{client_id}")
async def sync_get(client_id: str, request: Request):
    user = require_user(request)
    session = get_snapshot(user["id"], client_id)
    if session is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return {"session": session}


@router.put("/sessions/{client_id}")
async def sync_put(client_id: str, request: Request):
    user = require_user(request)
    if not CLIENT_ID_RE.match(client_id):
        return _bad("会话 ID 不合法")
    if not _content_length_ok(request, MAX_SESSION_BYTES + 1024 * 1024):
        return JSONResponse({"detail": "会话数据过大"}, status_code=413)
    if file_store.db_over_limit():
        return JSONResponse(
            {"detail": "服务器数据库空间已满，请删除不用的会话，或联系管理员清理/提高上限"}, status_code=507
        )
    try:
        payload = await request.json()
    except Exception:
        return _bad("请求体不是合法 JSON")
    try:
        session = _normalize_session(payload, client_id)
    except ValueError as exc:
        return _bad(str(exc))

    project = _is_settled(session)
    if request.query_params.get("project") == "0":
        project = False
    if request.query_params.get("project") == "1":
        project = True
    stored = upsert_snapshot(user["id"], session, project=project)
    return {"ok": True, "projected": project, "stored": stored}


@router.patch("/sessions/{client_id}")
async def sync_rename(client_id: str, payload: RenameIn, request: Request):
    user = require_user(request)
    if not CLIENT_ID_RE.match(client_id):
        return _bad("会话 ID 不合法")
    revision = rename_snapshot(user["id"], client_id, payload.title.strip())
    if revision is None:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    return {"ok": True, "updatedAt": revision}


@router.delete("/sessions/{client_id}")
async def sync_delete(client_id: str, request: Request):
    user = require_user(request)
    if not CLIENT_ID_RE.match(client_id):
        return _bad("会话 ID 不合法")
    if delete_snapshot(user["id"], client_id):
        audit(user["id"], "sync_delete", client_id, client_ip(request))
    return {"ok": True}


@router.post("/sessions/replace")
async def sync_replace(request: Request):
    user = require_user(request)
    if not _content_length_ok(request, MAX_REPLACE_BYTES):
        return JSONResponse({"detail": "导入数据过大"}, status_code=413)
    try:
        payload = await request.json()
    except Exception:
        return _bad("请求体不是合法 JSON")
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return _bad("sessions 必须是数组")
    if len(sessions) > MAX_SESSIONS:
        return _bad("会话数量超出限制")
    if sessions and file_store.db_over_limit():
        return JSONResponse(
            {"detail": "服务器数据库空间已满，请删除不用的会话，或联系管理员清理/提高上限"}, status_code=507
        )

    normalized: list[dict] = []
    seen: set[str] = set()
    try:
        for raw in sessions:
            client_id = str(raw.get("id") or "") if isinstance(raw, dict) else ""
            if not CLIENT_ID_RE.match(client_id):
                raise ValueError("会话 ID 不合法")
            if client_id in seen:
                raise ValueError("会话 ID 重复")
            seen.add(client_id)
            normalized.append(_normalize_session(raw, client_id))
    except ValueError as exc:
        return _bad(str(exc))

    count = replace_snapshots(user["id"], normalized)
    audit(user["id"], "sync_replace", f"count={count}", client_ip(request))
    return {"ok": True, "count": count}
