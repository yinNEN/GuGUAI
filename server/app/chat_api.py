from __future__ import annotations

import io
import logging
import wave
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import files as file_store
from .common import client_ip, require_user
from .config import settings
from .store import append_assistant, audit, get_api_key, prepare_turn, snapshot_exists
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

ASR_MAX_BYTES = 8 * 1024 * 1024
ASR_MAX_SECONDS = 120


def _validate_wav(data: bytes) -> None:
    """Reject malformed/non-WAV and overlong audio before forwarding it."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("录音必须是 WAV 文件")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise ValueError("录音必须是单声道 16-bit WAV")
            rate = audio.getframerate()
            if rate <= 0 or audio.getnframes() / rate > ASR_MAX_SECONDS:
                raise ValueError("录音时长超过 120 秒")
    except (wave.Error, EOFError) as exc:
        raise ValueError("WAV 文件格式无效") from exc


async def _forward_asr_file(api_key: str, name: str, data: bytes) -> Response:
    try:
        upstream = await get_client().post(
            upstream_url("/v1/tools/asr"),
            headers={**upstream_headers(api_key), "Accept": "application/json"},
            files={"file": (name, data, "audio/wav")},
        )
    except httpx.HTTPError as exc:
        return api_error(502, f"无法连接 DeepSeek ASR：{exc}", "network_error")
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )


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
                # TTS/ASR/file downloads are streamed directly. If the user
                # stops playback and the browser closes the response, close the
                # upstream connection immediately instead of continuing to pull
                # audio into a dead connection.
                if await request.is_disconnected():
                    break
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


@router.post("/api/speech/asr")
async def speech_asr(request: Request, file: UploadFile = File(...)):
    """Save a voice recording in the file buffer, then forward that stored copy to ASR.

    This mirrors normal attachment upload behavior: the recording is owned by the
    logged-in user, quota checked, recorded in stored_files, and later collected by
    the normal unreferenced-file cleanup. The browser never receives direct access
    to the server API key.
    """
    user = require_user(request)
    if not _check_length(request):
        return _too_large_error()
    api_key, _ = get_api_key()
    if not api_key:
        return _no_key_error()

    try:
        saved = await file_store.save_upload(
            user["id"],
            file,
            "voice_input",
            max_bytes=ASR_MAX_BYTES,
            validator=_validate_wav,
        )
    except file_store.FileError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
    file_id = str(saved["id"])
    if saved["bucket"] != "buffer":
        file_store.delete_file(user["id"], file_id)
        return api_error(400, "录音文件类型无效", "invalid_request_error")
    try:
        record, data = file_store.read_file_bytes(user["id"], file_id)
    except file_store.FileError:
        file_store.delete_file(user["id"], file_id)
        return api_error(400, "录音文件无效", "invalid_request_error")

    audit(user["id"], "voice_asr", f"{file_id} ({saved['size']} bytes)", client_ip(request))
    response = await _forward_asr_file(api_key, saved["name"], data)
    response.headers["X-Voice-File-Id"] = file_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/v1/tools/tts")
async def tts(request: Request):
    return await _forward(request, "/v1/tools/tts")
