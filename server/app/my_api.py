from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .common import require_user
from .store import get_conversation, list_user_conversations

router = APIRouter(prefix="/api/my", tags=["my"])


@router.get("/conversations")
async def my_conversations(request: Request):
    user = require_user(request)
    return {"conversations": list_user_conversations(user["id"])}


@router.get("/conversations/{conv_id}")
async def my_conversation_detail(conv_id: int, request: Request):
    user = require_user(request)
    conv = get_conversation(conv_id)
    if not conv or conv["user_id"] != user["id"]:
        return JSONResponse({"detail": "会话不存在"}, status_code=404)
    messages = []
    for msg in conv["messages"]:
        try:
            content = json.loads(msg["content"])
        except Exception:
            content = msg["content"]
        messages.append({**msg, "content": content})
    return {**conv, "messages": messages}
