from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from . import files
from .common import client_ip, require_admin, require_user
from .store import audit, delete_conversation

router = APIRouter(tags=["files"])

INLINE_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/avif",
}


def _error(exc: files.FileError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)


@router.post("/api/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    purpose: str | None = Form(default=None),
):
    user = require_user(request)
    try:
        result = await files.save_upload(user["id"], file, purpose)
    except files.FileError as exc:
        return _error(exc)
    audit(user["id"], "file_upload", f"{result['name']} ({result['size']} bytes, {result['bucket']})", client_ip(request))
    return result


@router.head("/api/files/{file_id}")
async def file_exists(file_id: str, request: Request):
    user = require_user(request)
    record = files.get_file(user["id"], file_id, admin=user["role"] == "admin")
    return Response(status_code=200 if record else 404)


@router.get("/api/files/{file_id}")
async def download_file(file_id: str, request: Request, inline: int = Query(default=0)):
    user = require_user(request)
    try:
        record, data = files.read_file_bytes(user["id"], file_id, admin=user["role"] == "admin")
    except files.FileError as exc:
        return _error(exc)
    mime = record.get("mime") or "application/octet-stream"
    safe_inline = bool(inline) and mime in INLINE_IMAGE_TYPES
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=60",
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(record['name'])}"
            if safe_inline
            else f"attachment; filename*=UTF-8''{quote(record['name'])}"
        ),
    }
    return Response(
        content=data,
        media_type=mime if safe_inline else "application/octet-stream",
        headers=headers,
    )


@router.get("/api/files/{file_id}/text")
async def text_file(file_id: str, request: Request):
    user = require_user(request)
    try:
        return files.read_text(user["id"], file_id, admin=user["role"] == "admin")
    except files.FileError as exc:
        return _error(exc)


@router.delete("/api/files/{file_id}")
async def delete_file(file_id: str, request: Request):
    user = require_user(request)
    deleted = files.delete_file(user["id"], file_id, admin=user["role"] == "admin")
    if deleted:
        audit(user["id"], "file_delete", file_id, client_ip(request))
    return {"ok": deleted}


# --------------------------------------------------------------------------
# 管理后台：存储配额与文件管理
# --------------------------------------------------------------------------
@router.get("/api/admin/storage")
async def storage_overview(request: Request):
    require_admin(request)
    return files.storage_stats()


@router.put("/api/admin/storage")
async def update_storage_limits(request: Request):
    admin = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求体不是合法 JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "请求体格式不正确"}, status_code=400)

    def size_mb(key: str) -> int | None:
        value = body.get(key)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须是数字") from None
        if number <= 0 or number > 1024 * 1024:
            raise ValueError(f"{key} 超出允许范围")
        return int(number * 1024 * 1024)

    try:
        limits = files.set_limits(
            buffer_bytes=size_mb("bufferMb"),
            text_bytes=size_mb("textMb"),
            db_bytes=size_mb("dbMb"),
        )
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    audit(admin["id"], "storage_limits", str(limits), client_ip(request))
    return {"ok": True, **files.storage_stats()}


@router.get("/api/admin/storage/files")
async def storage_files(
    request: Request,
    bucket: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    require_admin(request)
    rows, total = files.list_files(bucket=bucket, limit=limit, offset=offset)
    for row in rows:
        row["createdAt"] = row.pop("created_at")
        row["lastUsed"] = row.pop("last_used")
        row["deepseekId"] = row.pop("deepseek_id")
        row["references"] = int(row.pop("ref_count", 0) or 0)
    return {"files": rows, "total": total}


@router.delete("/api/admin/storage/files/{file_id}")
async def admin_delete_file(file_id: str, request: Request):
    admin = require_admin(request)
    references = files.reference_count(file_id)
    if references:
        # 被会话引用的文件只能随会话删除/整体替换（delete_unreferenced_files）
        # 或配额淘汰回收，后台不允许手动强制删除。
        return JSONResponse(
            {
                "detail": f"该文件仍被 {references} 个对话引用，不能手动删除；"
                "请先删除相关会话，由会话清理流程回收"
            },
            status_code=409,
        )
    deleted = files.delete_file(admin["id"], file_id, admin=True)
    if deleted:
        audit(admin["id"], "storage_file_delete", file_id, client_ip(request))
    return {"ok": deleted}


@router.post("/api/admin/storage/vacuum")
async def vacuum_storage(request: Request):
    admin = require_admin(request)
    result = files.vacuum_db()
    audit(
        admin["id"],
        "storage_vacuum",
        f"{result['before']} -> {result['after']}",
        client_ip(request),
    )
    return {"ok": True, **result, **files.storage_stats()}


@router.delete("/api/admin/conversations/{conv_id}")
async def admin_delete_conversation(conv_id: int, request: Request):
    admin = require_admin(request)
    deleted = delete_conversation(conv_id)
    if deleted:
        audit(admin["id"], "conversation_delete", str(conv_id), client_ip(request))
    return {"ok": deleted}
