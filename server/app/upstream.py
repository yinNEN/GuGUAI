from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import settings

logger = logging.getLogger("deepseek.proxy")

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=20.0,
                read=float(settings.upstream_read_timeout),
                write=120.0,
                pool=20.0,
            ),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=False,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def api_error(status_code: int, message: str, err_type: str = "api_error") -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


def upstream_headers(api_key: str, content_type: str | None = None, anthropic_beta: str | None = None) -> dict[str, str]:
    headers = {
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if anthropic_beta:
        headers["anthropic-beta"] = anthropic_beta
    return headers


def upstream_url(path: str) -> str:
    return f"{settings.upstream_base}{path}"


async def verify_api_key(api_key: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(upstream_url("/models"), headers=upstream_headers(api_key))
        if resp.status_code == 200:
            return True, "ok"
        detail = resp.text[:300]
        try:
            data = resp.json()
            detail = data.get("error", {}).get("message") or detail
        except Exception:
            pass
        return False, f"HTTP {resp.status_code}: {detail}"
    except Exception as exc:  # pragma: no cover - 网络异常
        return False, f"连接失败: {exc}"


# --------------------------------------------------------------------------
# SSE 汇总：把流式响应解析成可入库的助手消息
# --------------------------------------------------------------------------
class SSEAssembler:
    def __init__(self) -> None:
        self._buf = b""
        self._blocks: dict[int, dict[str, Any]] = {}
        self._order: list[int] = []
        self.usage: dict[str, int] = {}
        self.model: str | None = None
        self.stop_reason: str | None = None
        self.error: str | None = None
        self._done = False

    # ---- 字节流 ----
    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        while True:
            i = self._buf.find(b"\n\n")
            j = self._buf.find(b"\r\n\r\n")
            if i == -1 and j == -1:
                return
            if j != -1 and (i == -1 or j < i):
                block, self._buf = self._buf[:j], self._buf[j + 4:]
            else:
                block, self._buf = self._buf[:i], self._buf[i + 2:]
            self._handle_block(block)

    def _handle_block(self, block: bytes) -> None:
        event = None
        data_lines: list[str] = []
        for line in block.decode("utf-8", errors="replace").split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        try:
            obj = json.loads(raw)
        except Exception:
            return
        self._handle_event(event, obj)

    def _handle_event(self, event: str | None, obj: dict) -> None:
        etype = obj.get("type") or event
        if etype == "message_start":
            msg = obj.get("message") or {}
            self.model = msg.get("model") or self.model
            usage = msg.get("usage") or {}
            if isinstance(usage.get("input_tokens"), int):
                self.usage["input_tokens"] = usage["input_tokens"]
            for idx, block in enumerate(msg.get("content") or []):
                self._start_block(idx, block)
        elif etype == "content_block_start":
            self._start_block(obj.get("index", 0), obj.get("content_block") or {})
        elif etype == "content_block_delta":
            idx = obj.get("index", 0)
            delta = obj.get("delta") or {}
            block = self._blocks.get(idx)
            if block is None:
                block = {"type": "text", "text": ""}
                self._blocks[idx] = block
                self._order.append(idx)
            dtype = delta.get("type")
            if dtype == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text", ""))
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + str(delta.get("thinking", ""))
            elif dtype == "input_json_delta":
                block["_partial_json"] = block.get("_partial_json", "") + str(delta.get("partial_json", ""))
        elif etype == "content_block_stop":
            idx = obj.get("index", 0)
            block = self._blocks.get(idx)
            if block and block.get("_partial_json"):
                try:
                    block["input"] = json.loads(block["_partial_json"])
                except Exception:
                    block["input"] = {"_raw": block["_partial_json"][:2000]}
                block.pop("_partial_json", None)
        elif etype == "message_delta":
            delta = obj.get("delta") or {}
            if delta.get("stop_reason"):
                self.stop_reason = delta["stop_reason"]
            usage = obj.get("usage") or {}
            if isinstance(usage.get("output_tokens"), int):
                self.usage["output_tokens"] = usage["output_tokens"]
        elif etype == "error":
            err = obj.get("error") or {}
            self.error = err.get("message") or "上游返回错误"
        elif etype == "message_stop":
            self._done = True

    def _start_block(self, idx: int, block: dict) -> None:
        btype = block.get("type") or "text"
        item: dict[str, Any] = {"type": btype}
        if btype == "text":
            item["text"] = str(block.get("text", ""))
        elif btype == "thinking":
            item["thinking"] = str(block.get("thinking", ""))
            item["signature"] = block.get("signature")
        elif btype in ("tool_use", "server_tool_use"):
            item["name"] = block.get("name")
            item["input"] = block.get("input") or {}
        if idx not in self._blocks:
            self._order.append(idx)
        self._blocks[idx] = item

    # ---- 输出 ----
    def has_content(self) -> bool:
        return any(
            (b.get("text") or "").strip() or (b.get("thinking") or "").strip() or b.get("type") == "tool_use"
            for b in self._blocks.values()
        )

    def blocks(self) -> list[dict]:
        result: list[dict] = []
        for idx in sorted(self._order):
            block = dict(self._blocks[idx])
            btype = block.get("type")
            if btype == "text":
                if block.get("text"):
                    result.append({"type": "text", "text": block["text"]})
            elif btype == "thinking":
                if block.get("thinking"):
                    result.append({"type": "thinking", "thinking": block["thinking"]})
            elif btype == "tool_use":
                result.append(
                    {"type": "tool_use", "name": block.get("name"), "input": block.get("input") or {}}
                )
        return result

    def token_usage(self) -> tuple[int, int]:
        return int(self.usage.get("input_tokens", 0)), int(self.usage.get("output_tokens", 0))


# --------------------------------------------------------------------------
# 转发辅助
# --------------------------------------------------------------------------
async def relay_stream(
    request: Request,
    upstream: httpx.Response,
    on_complete,
) -> StreamingResponse:
    """把上游字节流原样转发，同时在结束后回调汇总结果。"""

    async def gen():
        assembler = SSEAssembler()
        completed = False
        try:
            async for chunk in upstream.aiter_bytes():
                assembler.feed(chunk)
                yield chunk
            completed = True
        finally:
            try:
                await upstream.aclose()
            except Exception:
                pass
            if completed:
                try:
                    on_complete(assembler)
                except Exception:
                    logger.exception("保存助手消息失败")
            else:
                logger.info("客户端中断，助手消息未保存")

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)
