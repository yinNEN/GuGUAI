from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import settings
from .db import db, reclaim_space
from .security import decrypt_secret, encrypt_secret

logger = logging.getLogger("deepseek.store")
API_KEY_SETTING = "deepseek_api_key"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 用户
# --------------------------------------------------------------------------
def get_user_by_username(username: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.role, u.is_active, u.must_change_password,
                   u.created_at, u.last_login_at,
                   (SELECT COUNT(*) FROM conversations c WHERE c.user_id = u.id) AS conversations,
                   (SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE c.user_id = u.id) AS messages
            FROM users u ORDER BY u.id
            """
        ).fetchall()
        return [dict(r) for r in rows]


def create_user(username: str, password_hash: str, role: str = "user", must_change_password: int = 0) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active, must_change_password, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (username, password_hash, role, must_change_password, now_iso()),
        )
        return int(cur.lastrowid)


def update_user(user_id: int, **fields: Any) -> None:
    allowed = {"role", "is_active", "must_change_password", "password_hash", "username"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    assignments = ", ".join(f"{k} = ?" for k in updates)
    with db() as conn:
        conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*updates.values(), user_id))


def delete_user(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    reclaim_space()


def touch_login(user_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user_id))


def count_admins(active_only: bool = True) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = 'admin'"
    if active_only:
        sql += " AND is_active = 1"
    with db() as conn:
        return int(conn.execute(sql).fetchone()[0])


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------
def create_session(token_hash: str, user_id: int, hours: int, ip: str, user_agent: str) -> str:
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash, user_id, now_iso(), expires, now_iso(), ip, user_agent[:400]),
        )
    return expires


def get_session_user(token_hash: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT s.token_hash, s.expires_at, u.id, u.username, u.role, u.is_active, u.must_change_password
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        if not data["is_active"]:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        try:
            expires = datetime.fromisoformat(data["expires_at"])
        except ValueError:
            expires = datetime.now(timezone.utc)
        if expires <= datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now_iso(), token_hash))
    return {
        "id": data["id"],
        "username": data["username"],
        "role": data["role"],
        "must_change_password": bool(data["must_change_password"]),
        "token_hash": token_hash,
    }


def delete_session(token_hash: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_user_sessions(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired_sessions() -> None:
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))


# --------------------------------------------------------------------------
# 配置项 / API Key
# --------------------------------------------------------------------------
def get_setting(key: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now_iso()),
        )


def delete_setting(key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def set_api_key(api_key: str) -> None:
    set_setting(API_KEY_SETTING, encrypt_secret(api_key))


def get_api_key() -> tuple[str | None, str]:
    """返回 (api_key, 来源)。来源为 database / env / none。"""
    stored = get_setting(API_KEY_SETTING)
    if stored:
        plain = decrypt_secret(stored)
        if plain:
            return plain, "database"
        logger.warning(
            "数据库中的 API Key 无法解密（APP_SECRET 或 data/secret.key 可能已变更），已回退到环境变量"
        )
    if settings.env_api_key:
        return settings.env_api_key, "env"
    return None, "none"


# --------------------------------------------------------------------------
# 审计日志
# --------------------------------------------------------------------------
def audit(user_id: int | None, action: str, detail: str = "", ip: str = "") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, detail, ip, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, action, detail[:2000], ip, now_iso()),
        )


def prune_audit_logs() -> None:
    """Keep audit history bounded (180 days / 100k rows) so it cannot grow forever."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat(timespec="seconds")
    with db() as conn:
        conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (cutoff,))
        conn.execute(
            "DELETE FROM audit_logs WHERE id NOT IN "
            "(SELECT id FROM audit_logs ORDER BY id DESC LIMIT 100000)"
        )
    reclaim_space()


def list_audit(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.user_id, u.username, a.action, a.detail, a.ip, a.created_at
            FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
            ORDER BY a.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 消息内容工具
# --------------------------------------------------------------------------
def message_text(content: Any) -> str:
    """把 Anthropic 风格 content 归一化为纯文本，用于生成指纹/标题。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text") or ""))
            elif btype == "thinking":
                continue  # 思考内容由服务端生成，客户端可能不回传
            elif btype in ("image", "document"):
                src = block.get("source") or {}
                marker = src.get("file_id") or src.get("media_type") or src.get("type", "")
                parts.append(f"[{btype}:{marker}]")
            elif btype == "tool_use":
                parts.append(f"[tool_use:{block.get('name', '')}]")
            elif btype == "tool_result":
                parts.append("[tool_result]")
                parts.append(message_text(block.get("content")))
        return "\n".join(p for p in parts if p)
    return ""


def fingerprint(role: str, content: Any) -> str:
    raw = f"{role}\n{message_text(content)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_content(value: Any) -> Any:
    """入库前去掉内联 base64 大字段并限制单条文本长度，避免数据库膨胀。"""
    if isinstance(value, dict):
        if value.get("type") == "base64" and isinstance(value.get("data"), str):
            return {
                **value,
                "data": f"[base64 omitted: {len(value['data'])} chars]",
            }
        return {k: sanitize_content(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_content(v) for v in value]
    if isinstance(value, str) and len(value) > 100_000:
        return value[:100_000] + f"…[truncated {len(value)} chars]"
    return value


def _title_from(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = " ".join(part for part in message_text(msg.get("content")).split() if part)
        if text:
            return text[:50]
    return "新会话"


# --------------------------------------------------------------------------
# 会话与消息
# --------------------------------------------------------------------------
def create_conversation(user_id: int, title: str, model: str | None) -> int:
    ts = now_iso()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (user_id, title, model, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (user_id, title or "新会话", model, ts, ts),
        )
        return int(cur.lastrowid)


def _last_message_role(conv_id: int) -> str | None:
    with db() as conn:
        row = conn.execute(
            "SELECT role FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (conv_id,)
        ).fetchone()
        return row["role"] if row else None


def _recent_conversations(user_id: int, limit: int = 60) -> list[dict]:
    with db() as conn:
        convs = [
            dict(r)
            for r in conn.execute(
                "SELECT id, model FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        ]
        if not convs:
            return []
        ids = [c["id"] for c in convs]
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT conversation_id, fingerprint FROM messages "
            f"WHERE role = 'user' AND conversation_id IN ({placeholders}) ORDER BY conversation_id, id",
            ids,
        ).fetchall()
    by_conv: dict[int, list[str]] = {cid: [] for cid in ids}
    for row in rows:
        by_conv[row["conversation_id"]].append(row["fingerprint"])
    for conv in convs:
        conv["user_fps"] = by_conv.get(conv["id"], [])
    return convs


def append_messages(conv_id: int, messages: list[dict]) -> None:
    if not messages:
        return
    ts = now_iso()
    with db() as conn:
        last = conn.execute(
            "SELECT role, fingerprint FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        last_role = last["role"] if last else None
        last_fp = last["fingerprint"] if last else None
        appended = 0
        for msg in messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            fp = fingerprint(role, content)
            if role == "user" and last_role == "user" and last_fp == fp:
                continue
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, fingerprint, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv_id, role, json.dumps(sanitize_content(content), ensure_ascii=False), fp, ts),
            )
            last_role, last_fp = role, fp
            appended += 1
        if appended:
            conn.execute(
                "UPDATE conversations SET updated_at = ?, "
                "message_count = (SELECT COUNT(*) FROM messages WHERE conversation_id = ?) WHERE id = ?",
                (ts, conv_id, conv_id),
            )


def append_assistant(
    conv_id: int,
    blocks: list[dict],
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    ts = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, fingerprint, created_at, tokens_in, tokens_out) "
            "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
            (
                conv_id,
                json.dumps(sanitize_content(blocks), ensure_ascii=False),
                fingerprint("assistant", blocks),
                ts,
                tokens_in,
                tokens_out,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ?, "
            "message_count = (SELECT COUNT(*) FROM messages WHERE conversation_id = ?) WHERE id = ?",
            (ts, conv_id, conv_id),
        )


def prepare_turn(user_id: int, messages: Any, model: str | None) -> int | None:
    """根据客户端上传的完整消息列表定位/创建会话，并落库新消息。

    匹配规则：以“用户消息序列”为会话指纹（助手回复允许被前端重新渲染），
    找到历史消息是本次上传前缀的最近会话；找不到则新建会话并完整保存本次历史。
    """
    if not isinstance(messages, list) or not messages:
        return None
    user_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    if not user_msgs:
        return None
    incoming_fps = [fingerprint("user", m.get("content")) for m in user_msgs]

    best: dict | None = None
    for conv in _recent_conversations(user_id):
        fps = conv["user_fps"]
        if not fps or len(fps) > len(incoming_fps):
            continue
        if incoming_fps[: len(fps)] == fps and (best is None or len(fps) > len(best["user_fps"])):
            best = conv

    if best is None:
        conv_id = create_conversation(user_id, _title_from(messages), model)
        append_messages(conv_id, messages)
        return conv_id

    conv_id = int(best["id"])
    n = len(best["user_fps"])
    last_user_idx = -1
    seen = 0
    for idx, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            seen += 1
            if seen == n:
                last_user_idx = idx
                break
    start = last_user_idx + 1
    if _last_message_role(conv_id) == "assistant" and start < len(messages) and messages[start].get("role") == "assistant":
        start += 1
    append_messages(conv_id, messages[start:])
    if model:
        with db() as conn:
            conn.execute("UPDATE conversations SET model = ? WHERE id = ?", (model, conv_id))
    return conv_id


def get_conversation(conv_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT c.*, u.username FROM conversations c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
            (conv_id,),
        ).fetchone()
        if not row:
            return None
        conv = dict(row)
        conv["messages"] = [
            dict(r)
            for r in conn.execute(
                "SELECT id, role, content, created_at, tokens_in, tokens_out "
                "FROM messages WHERE conversation_id = ? ORDER BY id",
                (conv_id,),
            ).fetchall()
        ]
        return conv


def list_user_conversations(user_id: int, limit: int = 100) -> list[dict]:
    with db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, title, model, created_at, updated_at, message_count FROM conversations "
                "WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        ]


def list_conversations(user_id: int | None, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    where = "WHERE c.user_id = ?" if user_id else ""
    args: tuple = (user_id,) if user_id else ()
    with db() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM conversations c {where}", args).fetchone()[0])
        rows = conn.execute(
            f"""
            SELECT c.id, c.title, c.model, c.created_at, c.updated_at, c.message_count,
                   c.user_id, u.username
            FROM conversations c JOIN users u ON u.id = c.user_id
            {where}
            ORDER BY c.updated_at DESC LIMIT ? OFFSET ?
            """,
            (*args, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows], total


def stats() -> dict:
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
    with db() as conn:
        return {
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "active_users": conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0],
            "conversations": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
            "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            "messages_today": conn.execute(
                "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (start_utc,)
            ).fetchone()[0],
        }


# --------------------------------------------------------------------------
# 会话快照同步（新版前端以服务端为准）
# --------------------------------------------------------------------------
def _ms_to_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return now_iso()


def snapshot_exists(user_id: int, client_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
        return row is not None


def list_snapshots(user_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT client_id, title, created_at, updated_at FROM session_snapshots "
            "WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [
            {
                "id": r["client_id"],
                "title": r["title"],
                "createdAt": r["created_at"],
                "updatedAt": r["updated_at"],
            }
            for r in rows
        ]


def get_snapshot(user_id: int, client_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            "SELECT data FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data"])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _project_content(message: dict) -> list:
    if message.get("role") == "assistant":
        blocks = message.get("blocks")
        return blocks if isinstance(blocks, list) else []
    parts: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        parts.append({"type": "text", "text": text})
    files = message.get("files")
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            server_id = f.get("serverId")
            if isinstance(server_id, str) and server_id:
                parts.append(
                    {
                        "type": "image" if f.get("isImage") else "document",
                        "source": {"type": "file", "file_id": server_id},
                    }
                )
            else:
                parts.append({"type": "file", "name": str(f.get("name") or "")})
    return parts


def _project_session(conn, user_id: int, session: dict) -> None:
    """把前端会话快照投影到 conversations / messages，供后台浏览与审计。"""
    client_id = str(session.get("id") or "")
    if not client_id:
        return
    messages = session.get("messages")
    messages = messages if isinstance(messages, list) else []
    title = str(session.get("title") or "") or "新会话"
    model = None
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("model"):
            model = str(m["model"])
            break
    created = _ms_to_iso(session.get("createdAt"))
    updated = _ms_to_iso(session.get("updatedAt"))

    row = conn.execute(
        "SELECT id FROM conversations WHERE user_id = ? AND client_id = ?",
        (user_id, client_id),
    ).fetchone()
    if row:
        conv_id = int(row["id"])
        conn.execute(
            "UPDATE conversations SET title = ?, model = ?, created_at = ?, updated_at = ?, "
            "message_count = ? WHERE id = ?",
            (title[:200], model, created, updated, len(messages), conv_id),
        )
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    else:
        cur = conn.execute(
            "INSERT INTO conversations "
            "(user_id, client_id, title, model, created_at, updated_at, message_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, client_id, title[:200], model, created, updated, len(messages)),
        )
        conv_id = int(cur.lastrowid)

    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        role = m["role"]
        content = _project_content(m)
        usage = m.get("usage") if isinstance(m.get("usage"), dict) else {}
        conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, fingerprint, created_at, tokens_in, tokens_out) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                conv_id,
                role,
                json.dumps(sanitize_content(content), ensure_ascii=False),
                fingerprint(role, content),
                _ms_to_iso(m.get("createdAt")),
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
            ),
        )



def _session_file_ids(session: dict) -> set[str]:
    """收集会话快照里引用本服务存储的附件 ID（serverId / source.file_id）。"""
    ids: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            server_id = value.get("serverId")
            if isinstance(server_id, str) and server_id:
                ids.add(server_id)
            if value.get("type") == "file" and isinstance(value.get("file_id"), str):
                ids.add(value["file_id"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(session.get("messages") or [])
    return ids


def _replace_session_refs(conn, user_id: int, client_id: str, session: dict) -> tuple[set[str], set[str]]:
    old = {
        row["file_id"]
        for row in conn.execute(
            "SELECT file_id FROM session_files WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchall()
    }
    new = _session_file_ids(session)
    conn.execute(
        "DELETE FROM session_files WHERE user_id = ? AND client_id = ?", (user_id, client_id)
    )
    conn.executemany(
        "INSERT OR IGNORE INTO session_files (user_id, client_id, file_id) VALUES (?, ?, ?)",
        [(user_id, client_id, file_id) for file_id in new],
    )
    return old, new


def backfill_session_files() -> None:
    """Repair missing attachment references for every existing snapshot.

    Older versions could leave a partially populated session_files table; a
    global "if any rows exist, skip" check would let purge_orphan_files delete
    legitimate message attachments that were never backfilled.
    """
    with db() as conn:
        rows = conn.execute("SELECT user_id, client_id, data FROM session_snapshots").fetchall()
        for row in rows:
            try:
                session = json.loads(row["data"])
            except Exception:
                continue
            if isinstance(session, dict):
                file_ids = _session_file_ids(session)
                conn.executemany(
                    "INSERT OR IGNORE INTO session_files (user_id, client_id, file_id) VALUES (?, ?, ?)",
                    [(row["user_id"], row["client_id"], file_id) for file_id in file_ids],
                )

def _server_file_ids(value: Any) -> dict[str, str]:
    """Map attachment object id -> server file id from a session snapshot."""
    ids: dict[str, str] = {}

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            file_id = item.get("id")
            server_id = item.get("serverId")
            if isinstance(file_id, str) and isinstance(server_id, str) and server_id:
                ids[file_id] = server_id
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return ids


def _merge_missing_server_ids(incoming: dict, existing: dict) -> None:
    """Keep file references when a stale device pushes an older file view.

    A device can still hold the pre-import copy of a session (same file IDs,
    serverId=null) while another device has imported and uploaded originals.
    If that device sends a newer session update, the server must not interpret
    the missing IDs as an intentional removal and delete the uploaded files.
    """
    known = _server_file_ids(existing)
    if not known:
        return

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            file_id = item.get("id")
            if isinstance(file_id, str) and not item.get("serverId") and file_id in known:
                item["serverId"] = known[file_id]
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(incoming.get("messages") or [])


def upsert_snapshot(user_id: int, session: dict, project: bool = True) -> bool:
    client_id = str(session["id"])
    title = str(session.get("title") or "")
    created = int(session.get("createdAt") or 0)
    updated = int(session.get("updatedAt") or created)
    data = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
    with db() as conn:
        existing = conn.execute(
            "SELECT updated_at, data FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
        # 多设备并发时不让较旧的快照覆盖较新的（相同时间戳允许更新标题等元数据）
        if existing is not None and updated < int(existing["updated_at"]):
            return False
        if existing is not None:
            try:
                previous_session = json.loads(existing["data"])
            except Exception:
                previous_session = None
            if isinstance(previous_session, dict):
                _merge_missing_server_ids(session, previous_session)
                data = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
        conn.execute(
            "INSERT INTO session_snapshots (user_id, client_id, title, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, client_id) DO UPDATE SET "
            "title = excluded.title, data = excluded.data, "
            "created_at = excluded.created_at, updated_at = excluded.updated_at",
            (user_id, client_id, title[:200], data, created, updated),
        )
        old_ids, new_ids = _replace_session_refs(conn, user_id, client_id, session)
        if project:
            _project_session(conn, user_id, session)
        removed_ids = old_ids - new_ids
    if removed_ids:
        from . import files as file_store

        file_store.delete_unreferenced_files(user_id, removed_ids)
    return True


def rename_snapshot(user_id: int, client_id: str, title: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT data FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
        if not row:
            return False
        try:
            data = json.loads(row["data"])
        except Exception:
            data = {}
        if isinstance(data, dict):
            data["title"] = title
            conn.execute(
                "UPDATE session_snapshots SET title = ?, data = ?, updated_at = ? "
                "WHERE user_id = ? AND client_id = ?",
                (
                    title[:200],
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    int(data.get("updatedAt") or 0) or int(datetime.now(timezone.utc).timestamp() * 1000),
                    user_id,
                    client_id,
                ),
            )
        conn.execute(
            "UPDATE conversations SET title = ? WHERE user_id = ? AND client_id = ?",
            (title[:200], user_id, client_id),
        )
        return True


def delete_conversation(conv_id: int) -> bool:
    """后台清理单条会话：删除快照、投影会话、附件引用及不再被引用的文件。"""
    file_ids: set[str] = set()
    user_id: int | None = None
    with db() as conn:
        row = conn.execute(
            "SELECT user_id, client_id FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if row is None:
            return False
        user_id = int(row["user_id"])
        client_id = row["client_id"]
        if client_id:
            snap = conn.execute(
                "SELECT data FROM session_snapshots WHERE user_id = ? AND client_id = ?",
                (user_id, client_id),
            ).fetchone()
            if snap:
                try:
                    file_ids = _session_file_ids(json.loads(snap["data"]))
                except Exception:
                    file_ids = set()
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        if client_id:
            conn.execute(
                "DELETE FROM session_snapshots WHERE user_id = ? AND client_id = ?",
                (user_id, client_id),
            )
            conn.execute(
                "DELETE FROM session_files WHERE user_id = ? AND client_id = ?",
                (user_id, client_id),
            )
    if user_id is not None and file_ids:
        from . import files as file_store

        file_store.delete_unreferenced_files(user_id, file_ids)
    reclaim_space()
    return True


def delete_snapshot(user_id: int, client_id: str) -> bool:
    file_ids: set[str] = set()
    with db() as conn:
        snap = conn.execute(
            "SELECT data FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        ).fetchone()
        if snap:
            try:
                file_ids = _session_file_ids(json.loads(snap["data"]))
            except Exception:
                file_ids = set()
        cur = conn.execute(
            "DELETE FROM session_snapshots WHERE user_id = ? AND client_id = ?",
            (user_id, client_id),
        )
        conn.execute(
            "DELETE FROM conversations WHERE user_id = ? AND client_id = ?", (user_id, client_id)
        )
        conn.execute(
            "DELETE FROM session_files WHERE user_id = ? AND client_id = ?", (user_id, client_id)
        )
    if file_ids:
        from . import files as file_store

        file_store.delete_unreferenced_files(user_id, file_ids)
    reclaim_space()
    return cur.rowcount > 0


def replace_snapshots(user_id: int, sessions: list[dict]) -> int:
    """用给定快照替换该用户的全部会话（历史导入 / 删除全部对话）。"""
    old_ids: set[str] = set()
    with db() as conn:
        for row in conn.execute(
            "SELECT data FROM session_snapshots WHERE user_id = ?", (user_id,)
        ).fetchall():
            try:
                old_ids |= _session_file_ids(json.loads(row["data"]))
            except Exception:
                continue
        conn.execute("DELETE FROM session_snapshots WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM session_files WHERE user_id = ?", (user_id,))
        new_ids: set[str] = set()
        for session in sessions:
            client_id = str(session["id"])
            title = str(session.get("title") or "")
            created = int(session.get("createdAt") or 0)
            updated = int(session.get("updatedAt") or created)
            conn.execute(
                "INSERT INTO session_snapshots (user_id, client_id, title, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, client_id) DO UPDATE SET "
                "title = excluded.title, data = excluded.data, updated_at = excluded.updated_at",
                (
                    user_id,
                    client_id,
                    title[:200],
                    json.dumps(session, ensure_ascii=False, separators=(",", ":")),
                    created,
                    updated,
                ),
            )
            ids = _session_file_ids(session)
            new_ids |= ids
            conn.executemany(
                "INSERT OR IGNORE INTO session_files (user_id, client_id, file_id) VALUES (?, ?, ?)",
                [(user_id, client_id, file_id) for file_id in ids],
            )
            _project_session(conn, user_id, session)
    removed = old_ids - new_ids
    if removed:
        from . import files as file_store

        file_store.delete_unreferenced_files(user_id, removed)
    reclaim_space()
    return len(sessions)
