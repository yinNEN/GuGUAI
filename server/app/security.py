from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from collections import defaultdict, deque

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_fernet_lock = threading.Lock()
_fernet: Fernet | None = None


# --------------------------------------------------------------------------
# 密码
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码过长（UTF-8 编码后不能超过 72 字节）")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# 会话令牌
# --------------------------------------------------------------------------
def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 敏感配置加密（API Key 落库前加密）
# --------------------------------------------------------------------------
def _build_fernet() -> Fernet:
    raw = os.getenv("APP_SECRET", "").strip()
    if raw:
        # 允许直接给 Fernet Key，也允许给任意长口令（派生为 Key）
        try:
            return Fernet(raw.encode("ascii"))
        except Exception:
            digest = hashlib.sha256(raw.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))

    key_file = settings.secret_key_file
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        key = key_file.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_file.write_bytes(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return Fernet(key)


def get_fernet() -> Fernet:
    global _fernet
    with _fernet_lock:
        if _fernet is None:
            _fernet = _build_fernet()
        return _fernet


def encrypt_secret(plaintext: str) -> str:
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str | None:
    try:
        return get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 10:
        return "*" * len(secret)
    return f"{secret[:6]}{'*' * 8}{secret[-4:]}"


# --------------------------------------------------------------------------
# 登录限流（进程内；单进程部署即可满足需求）
# --------------------------------------------------------------------------
class LoginRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> int:
        """返回 0 表示允许；否则返回需要等待的秒数。"""
        now = time.monotonic()
        window = settings.login_window_seconds
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= settings.login_max_attempts:
                return max(1, int(window - (now - q[0])) + 1)
        return 0

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_limiter = LoginRateLimiter()
