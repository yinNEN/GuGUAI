from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import httpx
from fastapi import UploadFile

from .config import settings
from .db import db, reclaim_space
from .store import get_api_key, get_setting, set_setting

FILE_MAX_BYTES = 32 * 1024 * 1024
TEXT_PREVIEW_BYTES = 2 * 1024 * 1024

DEFAULT_FILE_BUFFER_BYTES = 2 * 1024**3
DEFAULT_TEXT_STORE_BYTES = 1 * 1024**3
DEFAULT_DB_MAX_BYTES = 4 * 1024**3

LIMIT_KEYS = {
    "buffer": "file_buffer_max_bytes",
    "text": "text_store_max_bytes",
    "db": "db_max_bytes",
}

TEXT_EXTS = {
    "txt", "text", "md", "markdown", "rst", "log", "csv", "tsv",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hxx", "m", "mm",
    "py", "pyw", "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte",
    "java", "kt", "kts", "scala", "groovy", "go", "rs", "rb", "php", "cs",
    "swift", "dart", "lua", "pl", "r", "jl", "sql", "sh", "bash", "zsh",
    "bat", "cmd", "ps1", "psm1", "ini", "cfg", "conf", "toml", "yaml", "yml",
    "json", "jsonc", "xml", "html", "htm", "css", "scss", "less", "sass",
    "svg", "tex", "bib", "makefile", "dockerfile", "gitignore", "env",
    "properties", "gradle", "cmake", "asm", "s", "v", "vhd", "vhdl",
}

TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/x-sh",
    "application/x-httpd-php",
    "application/sql",
    "image/svg+xml",
}


class FileError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class FileTooLarge(FileError):
    status_code = 413


class FileQuotaExceeded(FileError):
    status_code = 507


class FileNotFound(FileError):
    status_code = 404


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def get_ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot + 1 :].lower() if dot > 0 else ""


def sanitize_name(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    base = base.replace("\\", "_").replace("/", "_")
    if not base or base in (".", ".."):
        base = "file"
    return base[:180]


def is_text_file(name: str, mime: str = "") -> bool:
    ext = get_ext(name)
    if ext in TEXT_EXTS:
        return True
    if name.lower() in {"makefile", "dockerfile", "readme", "license"}:
        return True
    mime = (mime or "").lower()
    return mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_TYPES


def get_limit(kind: str) -> int:
    defaults = {
        "buffer": DEFAULT_FILE_BUFFER_BYTES,
        "text": DEFAULT_TEXT_STORE_BYTES,
        "db": DEFAULT_DB_MAX_BYTES,
    }
    raw = get_setting(LIMIT_KEYS[kind])
    try:
        return int(raw) if raw is not None else defaults[kind]
    except (TypeError, ValueError):
        return defaults[kind]


def set_limits(buffer_bytes: int | None = None, text_bytes: int | None = None, db_bytes: int | None = None) -> dict:
    if buffer_bytes is not None:
        set_setting(LIMIT_KEYS["buffer"], str(max(64 * 1024 * 1024, buffer_bytes)))
    if text_bytes is not None:
        set_setting(LIMIT_KEYS["text"], str(max(64 * 1024 * 1024, text_bytes)))
    if db_bytes is not None:
        set_setting(LIMIT_KEYS["db"], str(max(128 * 1024 * 1024, db_bytes)))
    return {"buffer": get_limit("buffer"), "text": get_limit("text"), "db": get_limit("db")}


def _base_dir(bucket: str) -> Path:
    base = settings.text_dir if bucket == "text" else settings.files_dir
    base.mkdir(parents=True, exist_ok=True)
    return base


def _path_for(file_id: str, bucket: str) -> Path:
    return _base_dir(bucket) / file_id


def _record_path(row: Any) -> Path:
    name = str(row["path"] or "")
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise FileNotFound("文件路径无效")
    base = _base_dir(str(row["bucket"] or "buffer")).resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        raise FileNotFound("文件路径无效")
    return path


def _remove_file(conn, row: Any) -> None:
    try:
        _record_path(row).unlink(missing_ok=True)
    except (OSError, FileNotFound):
        pass
    conn.execute("DELETE FROM session_files WHERE file_id = ?", (row["id"],))
    conn.execute("DELETE FROM stored_files WHERE id = ?", (row["id"],))


def buffer_usage() -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM stored_files WHERE bucket = 'buffer'"
        ).fetchone()
        return int(row[0])


def text_usage() -> int:
    with db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM stored_files WHERE bucket = 'text'"
        ).fetchone()
        return int(row[0])


def _evict_bucket(conn, bucket: str, incoming: int) -> None:
    """超出上限时按最久未使用/最旧顺序删除文件，为新文件腾出空间。"""
    limit = get_limit(bucket)
    usage = int(
        conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM stored_files WHERE bucket = ?", (bucket,)
        ).fetchone()[0]
    )
    while usage + incoming > limit:
        row = conn.execute(
            "SELECT * FROM stored_files WHERE bucket = ? ORDER BY created_at ASC, last_used ASC LIMIT 1",
            (bucket,),
        ).fetchone()
        if row is None:
            label = "文本存储区" if bucket == "text" else "文件缓冲区"
            raise FileQuotaExceeded(f"{label}已满，且没有可清理的旧文件")
        usage -= int(row["size"])
        _remove_file(conn, row)


async def save_upload(user_id: int, upload: UploadFile, purpose: str | None = None) -> dict:
    name = sanitize_name(upload.filename or "file")
    mime = (upload.content_type or mimetypes.guess_type(name)[0] or "").strip()[:200]
    purpose = (purpose or "").strip()[:64] or None
    text = is_text_file(name, mime)
    bucket = "text" if text else "buffer"
    if bucket == "text" and purpose is None:
        purpose = "user_data"

    settings.files_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = settings.files_dir / f".tmp-{uuid.uuid4().hex}"
    target: Path | None = None
    size = 0
    digest = hashlib.sha256()
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > FILE_MAX_BYTES:
                    raise FileTooLarge("单个文件不能超过 32MB")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise FileError("不能上传空文件")

        file_id = uuid.uuid4().hex
        target = _path_for(file_id, bucket)
        os.replace(tmp_path, target)
        now = _now_ms()
        with db() as conn:
            # 缓冲区与文本区都按配额淘汰最旧文件，不再直接拒绝上传
            _evict_bucket(conn, bucket, size)
            conn.execute(
                "INSERT INTO stored_files "
                "(id, user_id, bucket, name, size, mime, created_at, last_used, purpose, path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, user_id, bucket, name, size, mime, now, now, purpose, file_id),
            )
        return {
            "id": file_id,
            "name": name,
            "size": size,
            "mime": mime,
            "isImage": mime.startswith("image/"),
            "bucket": bucket,
            "sha256": digest.hexdigest(),
        }
    except Exception:
        tmp_path.unlink(missing_ok=True)
        if target is not None:
            target.unlink(missing_ok=True)
        raise


def get_file(user_id: int, file_id: str, admin: bool = False) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM stored_files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            return None
        if not admin and int(row["user_id"]) != int(user_id):
            return None
        return dict(row)


def touch_file(file_id: str) -> None:
    with db() as conn:
        conn.execute("UPDATE stored_files SET last_used = ? WHERE id = ?", (_now_ms(), file_id))


def read_file_bytes(user_id: int, file_id: str, admin: bool = False) -> tuple[dict, bytes]:
    record = get_file(user_id, file_id, admin=admin)
    if record is None:
        raise FileNotFound("文件不存在")
    path = _record_path(record)
    if not path.exists():
        raise FileNotFound("文件已被清理，请重新上传")
    touch_file(file_id)
    return record, path.read_bytes()


def read_text(user_id: int, file_id: str, admin: bool = False) -> dict:
    record = get_file(user_id, file_id, admin=admin)
    if record is None:
        raise FileNotFound("文件不存在")
    if record["bucket"] != "text":
        raise FileError("该文件不是文本文件")
    path = _record_path(record)
    if not path.exists():
        raise FileNotFound("文件已被清理，请重新上传")
    with open(path, "rb") as fh:
        data = fh.read(TEXT_PREVIEW_BYTES + 1)
    truncated = len(data) > TEXT_PREVIEW_BYTES
    if truncated:
        data = data[:TEXT_PREVIEW_BYTES]
    text = data.decode("utf-8", errors="replace")
    touch_file(file_id)
    return {
        "id": record["id"],
        "name": record["name"],
        "size": record["size"],
        "text": text,
        "truncated": truncated,
    }


def delete_user_files(user_id: int) -> None:
    with db() as conn:
        rows = conn.execute("SELECT * FROM stored_files WHERE user_id = ?", (user_id,)).fetchall()
        for row in rows:
            try:
                _record_path(row).unlink(missing_ok=True)
            except (OSError, FileNotFound):
                pass
        conn.execute("DELETE FROM session_files WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM stored_files WHERE user_id = ?", (user_id,))


def purge_orphan_files(max_age_seconds: int = 24 * 60 * 60) -> int:
    """Delete uploads that were never referenced by a session.

    Draft uploads are linked only when the session snapshot is pushed. A user
    who cancels or closes the page leaves no reference; keep a grace period so
    an in-progress draft is not removed.
    """
    cutoff = int(time.time() * 1000) - max_age_seconds * 1000
    removed = 0
    with db() as conn:
        rows = conn.execute(
            "SELECT f.* FROM stored_files f "
            "LEFT JOIN session_files sf "
            "ON sf.user_id = f.user_id AND sf.file_id = f.id "
            "WHERE sf.file_id IS NULL AND f.created_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            try:
                _record_path(row).unlink(missing_ok=True)
            except (OSError, FileNotFound):
                pass
            conn.execute("DELETE FROM stored_files WHERE id = ?", (row["id"],))
            removed += 1
    if removed:
        reclaim_space()
    return removed


def delete_file(user_id: int, file_id: str, admin: bool = False) -> bool:
    record = get_file(user_id, file_id, admin=admin)
    if record is None:
        return False
    try:
        _record_path(record).unlink(missing_ok=True)
    except OSError:
        pass
    with db() as conn:
        conn.execute("DELETE FROM session_files WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM stored_files WHERE id = ?", (file_id,))
    return True


def delete_unreferenced_files(user_id: int, file_ids: Iterable[str]) -> int:
    """会话删除/替换后，删除不再被该用户任何会话引用的缓冲区/文本文件。"""
    removed = 0
    seen: set[str] = set()
    for file_id in file_ids:
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        with db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM stored_files WHERE id = ? AND user_id = ?", (file_id, user_id)
            ).fetchone()
            if exists is None:
                continue
            referenced = conn.execute(
                "SELECT 1 FROM session_files WHERE user_id = ? AND file_id = ? LIMIT 1",
                (user_id, file_id),
            ).fetchone()
            if referenced is not None:
                continue
            row = conn.execute("SELECT * FROM stored_files WHERE id = ?", (file_id,)).fetchone()
            if row is not None:
                try:
                    _record_path(row).unlink(missing_ok=True)
                except (OSError, FileNotFound):
                    pass
                conn.execute("DELETE FROM stored_files WHERE id = ?", (file_id,))
                removed += 1
    return removed


def list_files(bucket: str | None = None, user_id: int | None = None, limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    where: list[str] = []
    args: list[Any] = []
    if bucket in ("buffer", "text"):
        where.append("f.bucket = ?")
        args.append(bucket)
    if user_id is not None:
        where.append("f.user_id = ?")
        args.append(user_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with db() as conn:
        total = int(
            conn.execute(f"SELECT COUNT(*) FROM stored_files f {clause}", args).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT f.id, f.user_id, u.username, f.bucket, f.name, f.size, f.mime,
                   f.created_at, f.last_used, f.deepseek_id
            FROM stored_files f JOIN users u ON u.id = f.user_id
            {clause}
            ORDER BY f.created_at DESC LIMIT ? OFFSET ?
            """,
            (*args, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows], total


def db_size() -> int:
    with db() as conn:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    total = page_count * page_size
    for suffix in ("-wal", "-shm"):
        try:
            total += Path(str(settings.db_path) + suffix).stat().st_size
        except OSError:
            pass
    return total


def db_over_limit() -> bool:
    return db_size() >= get_limit("db")


def storage_stats() -> dict:
    with db() as conn:
        counts = {
            row["bucket"]: int(row["n"])
            for row in conn.execute(
                "SELECT bucket, COUNT(*) AS n FROM stored_files GROUP BY bucket"
            ).fetchall()
        }
    return {
        "db": {"used": db_size(), "limit": get_limit("db")},
        "buffer": {
            "used": buffer_usage(),
            "limit": get_limit("buffer"),
            "files": counts.get("buffer", 0),
        },
        "text": {
            "used": text_usage(),
            "limit": get_limit("text"),
            "files": counts.get("text", 0),
        },
        "fileMaxBytes": FILE_MAX_BYTES,
    }


def vacuum_db() -> dict:
    before = db_size()
    with db() as conn:
        conn.execute("VACUUM")
    return {"before": before, "after": db_size()}


# --------------------------------------------------------------------------
# DeepSeek Files API 映射：客户端用本服务的文件 ID，转发前换成 DeepSeek file_id
# --------------------------------------------------------------------------
_upload_locks: dict[str, asyncio.Lock] = {}


async def _ensure_deepseek(record: dict) -> str:
    if record.get("deepseek_id"):
        return str(record["deepseek_id"])
    lock = _upload_locks.setdefault(record["id"], asyncio.Lock())
    async with lock:
        current = get_file(int(record["user_id"]), record["id"], admin=True)
        if current is None:
            raise FileNotFound("文件不存在")
        if current.get("deepseek_id"):
            return str(current["deepseek_id"])
        path = _record_path(current)
        if not path.exists():
            raise FileNotFound("文件已被清理，请重新上传")
        api_key, _ = get_api_key()
        if not api_key:
            raise FileError("服务器尚未配置 DeepSeek API Key", 503)
        headers = {
            "x-api-key": api_key,
            "anthropic-beta": "files-api-2025-04-14",
        }
        data = {"purpose": current.get("purpose")} if current.get("purpose") else {}
        try:
            content = path.read_bytes()
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{settings.upstream_base}/anthropic/v1/files",
                    headers=headers,
                    data=data,
                    files={
                        "file": (
                            current["name"],
                            content,
                            current.get("mime") or "application/octet-stream",
                        )
                    },
                )
        except httpx.HTTPError as exc:
            raise FileError(f"上传到 DeepSeek 失败：{exc}", 502) from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise FileError(f"上传到 DeepSeek 失败（HTTP {response.status_code}）：{detail}", 502)
        try:
            remote_id = response.json()["id"]
        except Exception:
            raise FileError("DeepSeek 返回的文件 ID 无效", 502) from None
        with db() as conn:
            conn.execute(
                "UPDATE stored_files SET deepseek_id = ?, last_used = ? WHERE id = ?",
                (str(remote_id), _now_ms(), current["id"]),
            )
        return str(remote_id)


async def prepare_outbound_payload(user_id: int, payload: dict) -> dict:
    """把消息里的本服务文件 ID 换成 DeepSeek file_id，必要时先补传。"""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    changed = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in ("image", "document"):
                continue
            source = block.get("source")
            if not isinstance(source, dict) or source.get("type") != "file":
                continue
            file_id = source.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                continue
            record = get_file(user_id, file_id)
            if record is None:
                # 导入历史里的 DeepSeek file_id 直接放行
                continue
            remote_id = await _ensure_deepseek(record)
            if remote_id != file_id:
                source["file_id"] = remote_id
                changed = True
            touch_file(file_id)
    return payload if changed else payload
