from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

SERVER_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = SERVER_DIR.parent

# .env 可以放在 server/ 或项目根目录
load_dotenv(SERVER_DIR / ".env")
load_dotenv(PROJECT_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None and raw.strip() else default
    except ValueError:
        return default


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    p = Path(raw).expanduser() if raw else default
    if not p.is_absolute():
        p = SERVER_DIR / p
    return p.resolve()


class Settings:
    def __init__(self) -> None:
        self.server_dir = SERVER_DIR
        self.project_dir = PROJECT_DIR
        self.pages_dir = SERVER_DIR / "pages"
        self.web_dir = _path("WEB_DIR", PROJECT_DIR / "web")
        self.data_dir = _path("DATA_DIR", SERVER_DIR / "data")
        self.db_path = self.data_dir / "chat.db"
        self.secret_key_file = self.data_dir / "secret.key"
        self.files_dir = self.data_dir / "files"
        self.text_dir = self.data_dir / "text"

        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = _env_int("PORT", 8000)
        self.upstream_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.env_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

        self.cookie_name = os.getenv("SESSION_COOKIE_NAME", "ds_chat_session")
        self.cookie_secure = _env_bool("COOKIE_SECURE", False)
        self.session_ttl_hours = _env_int("SESSION_TTL_HOURS", 168)
        self.login_max_attempts = _env_int("LOGIN_MAX_ATTEMPTS", 10)
        self.login_window_seconds = _env_int("LOGIN_WINDOW_SECONDS", 300)

        self.admin_username = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
        self.admin_password = os.getenv("ADMIN_PASSWORD", "")

        self.max_body_mb = _env_int("MAX_BODY_MB", 100)
        self.upstream_read_timeout = _env_int("UPSTREAM_READ_TIMEOUT", 600)
        self.allowed_origins = [
            o.strip().rstrip("/")
            for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        ]

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
