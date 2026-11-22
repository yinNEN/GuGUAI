from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import files as file_store
from .common import require_user
from .config import settings
from .store import append_assistant, get_api_key, prepare_turn, snapshot_exists
from .upstream import (
    api_error,
    get_client,
    relay_stream,
    upstream_headers,
    upstream_url,
)

router = APIRouter(tags=["deepseek-proxy"])


def _no_key_error() -> JSONResponse:
    return api_error(
        503,
        "服务器尚未配置 DeepSeek API Key，请联系管理员在后台配置。",
        "configuration_error",
    )


def _too_large_error() -> JSONResponse:
    return api_error(413, f"请求体过大（上限 {settings.max_body_mb} MB）", "invalid_request_error")


def _check_length(request: Request) -> bool:
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    return length <= settings.max_body_mb * 1024 * 1024


@router.get("/models")
async def models(request: Request):
    require_user(request)
    api_key, _ = get_api_key()
    if not api_key:
        return _no_key_error()
    try:
        resp = await get_client().get(
            upstream_url("/models"),
            headers={**upstream_headers(api_key), "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        return api_error(502, f"无法连接 DeepSeek API：{exc}", "network_error")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@router.post("/anthropic/v1/messages")
async def messages(request: Request):
    user = require_user(request)
    if not _check_length(request):
        return _too_large_error()

    api_key, _ = get_api_key()
    if not api_key:
        return _no_key_error()

    if file_store.db_over_limit():
        return api_error(
            507,
            "服务器数据库空间已满，请删除不用的会话，或联系管理员清理/提高上限。",
            "storage_limit_error",
        )

    try:
        payload = await request.json()
    except Exception:
        return api_error(400, "请求体不是合法的 JSON", "invalid_request_error")
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return api_error(400, "请求体缺少 messages 字段", "invalid_request_error")

    # 1) 新版前端会用 X-Chat-Session-Id 标识会话，并已把完整会话快照同步到服务器；
    #    此时以快照为准，避免代理再按前缀匹配生成一条重复会话。旧客户端仍走原来的归档逻辑。
    session_id = (request.headers.get("x-chat-session-id") or "").strip()
    try:
        if session_id and snapshot_exists(user["id"], session_id):
            conv_id = None
        else:
            conv_id = prepare_turn(user["id"], payload["messages"], payload.get("model"))
    except Exception:
        # 存储失败不应阻断对话，但需要留下日志
        logging.getLogger("deepseek.proxy").exception("保存用户消息失败")
        conv_id = None

    # 附件由本服务缓冲：转发前把本服务文件 ID 换成 DeepSeek file_id（必要时补传）
    try:
        outbound = await file_store.prepare_outbound_payload(user["id"], payload)
    except file_store.FileError as exc:
        return api_error(exc.status_code, str(exc), "file_error")

    headers = upstream_headers(
        api_key,
        content_type="application/json",
        anthropic_beta=request.headers.get("anthropic-beta"),
    )
    headers["Accept"] = "text/event-stream"

    client = get_client()
    try:
        upstream = await client.send(
            client.build_request(
                "POST",
                upstream_url("/anthropic/v1/messages"),
                headers=headers,
                json=outbound,
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        return api_error(502, f"无法连接 DeepSeek API：{exc}", "network_error")

    if upstream.status_code >= 400:
        body = await upstream.aread()
        await upstream.aclose()
        return Response(
            content=body,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    def on_complete(assembler) -> None:
        if conv_id is None:
            return
        blocks = assembler.blocks()
        if not blocks:
            return
        tokens_in, tokens_out = assembler.token_usage()
        append_assistant(conv_id, blocks, tokens_in, tokens_out)

    return await relay_stream(request, upstream, on_complete)


async def _forward(request: Request, path: str):
    require_user(request)
    if not _check_length(request):
        return _too_large_error()
    api_key, _ = get_api_key()
    if not api_key:
        return _no_key_error()

    body = await request.body()
    headers = upstream_headers(
        api_key,
        content_type=request.headers.get("content-type"),
        anthropic_beta=request.headers.get("anthropic-beta"),
    )
    headers["Accept"] = request.headers.get("accept", "*/*")

    client = get_client()
    try:
        upstream = await client.send(
            client.build_request(request.method, upstream_url(path), headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as exc:
        return api_error(502, f"无法连接 DeepSeek API：{exc}", "network_error")

    if upstream.status_code >= 400:
        data = await upstream.aread()
        await upstream.aclose()
        return Response(
            content=data,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    response_headers = {}
    disposition = upstream.headers.get("content-disposition")
    if disposition:
        response_headers["Content-Disposition"] = disposition

    async def gen():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        gen(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=response_headers,
    )


@router.post("/anthropic/v1/files")
async def files(request: Request):
    return await _forward(request, "/anthropic/v1/files")


@router.delete("/anthropic/v1/files/{file_id}")
async def delete_file(file_id: str, request: Request):
    return await _forward(request, f"/anthropic/v1/files/{quote(file_id, safe='')}")


@router.post("/v1/tools/asr")
async def asr(request: Request):
    return await _forward(request, "/v1/tools/asr")


@router.post("/v1/tools/tts")
async def tts(request: Request):
    return await _forward(request, "/v1/tools/tts")
