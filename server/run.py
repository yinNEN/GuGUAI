"""DeepSeek 私有化聊天服务启动入口。

用法：
    python run.py            # 使用 .env / 环境变量配置
    python run.py --port 8080
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

import uvicorn

from app.config import settings


def setup_logging() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # pythonw / 无控制台环境下 sys.stderr 为 None，跳过控制台输出
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.data_dir / "server.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek 私有化聊天服务")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    setup_logging()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
