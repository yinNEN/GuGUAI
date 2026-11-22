"""本地联调用的 DeepSeek 上游 Mock（仅用于测试，不参与生产部署）。

启动：
    python tests/mock_upstream.py
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()


@app.get("/models")
async def models():
    return {"object": "list", "data": [{"id": "deepseek-flash", "object": "model"}]}


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


@app.post("/anthropic/v1/messages")
async def messages(request: Request):
    payload = await request.json()
    user_text = ""
    for msg in reversed(payload.get("messages") or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            user_text = content
        elif isinstance(content, list):
            user_text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        break
    reply = f"[mock] {user_text}"

    async def gen():
        yield _sse("message_start", {
            "type": "message_start",
            "message": {"id": "msg_mock", "model": payload.get("model", "deepseek-flash"),
                        "usage": {"input_tokens": 11}, "content": []},
        })
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        })
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "先想一下"},
        })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": 1,
            "content_block": {"type": "text", "text": ""},
        })
        for ch in reply:
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": 1,
                "delta": {"type": "text_delta", "text": ch},
            })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 1})
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": len(reply)},
        })
        yield _sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/anthropic/v1/files")
async def files(request: Request):
    body = await request.body()
    return {"id": "file_mock_1", "type": "file", "bytes": len(body)}


@app.post("/v1/tools/asr")
async def asr():
    return {"text": "语音转写结果"}


@app.post("/v1/tools/tts")
async def tts():
    return Response(b"ID3mockaudio", media_type="audio/mpeg")


@app.get("/healthz")
async def healthz():
    return {"ok": True}
