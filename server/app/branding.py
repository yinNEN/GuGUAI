from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response

from .config import settings
from .store import get_setting, set_setting

router = APIRouter(prefix="/api/public", tags=["public"])

ICON_MAX_BYTES = 1024 * 1024
DEFAULTS = {
    "title": "GuGuAI",
    "subtitle": "Powered by Deepseek.",
    "mark": "G",
    "color": "#4d6bfe",
    "mark_mode": "text",
}
KEYS = {
    "title": "branding_title",
    "subtitle": "branding_subtitle",
    "mark": "branding_mark",
    "color": "branding_color",
    "mark_mode": "branding_mark_mode",
}
ICON_EXT_KEY = "branding_icon_ext"
ICON_VERSION_KEY = "branding_icon_version"
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ICON_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def _icon_dir() -> Path:
    path = settings.data_dir / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _icon_ext() -> str:
    ext = (get_setting(ICON_EXT_KEY) or "").lower()
    return ext if ext in ICON_TYPES else ""


def get_branding() -> dict[str, str]:
    result = dict(DEFAULTS)
    for field, key in KEYS.items():
        value = get_setting(key)
        if value:
            result[field] = value
    if result["mark_mode"] not in ("text", "image", "none"):
        result["mark_mode"] = "text"
    if not COLOR_RE.match(result["color"]):
        result["color"] = DEFAULTS["color"]
    ext = _icon_ext()
    has_icon = bool(ext and ( _icon_dir() / f"icon.{ext}").is_file())
    if result["mark_mode"] == "image" and not has_icon:
        result["mark_mode"] = "text"
    result["has_icon"] = has_icon
    version = get_setting(ICON_VERSION_KEY) or "0"
    result["mark_image"] = f"/api/public/branding/icon?v={version}" if has_icon else ""
    return result


def save_branding(title: str, subtitle: str, mark: str, color: str, mark_mode: str) -> dict[str, str]:
    title = title.strip()[:80] or DEFAULTS["title"]
    subtitle = subtitle.strip()[:120]
    mark = mark.strip()[:4]
    color = color.strip()
    if mark_mode not in ("text", "image", "none"):
        raise ValueError("品牌标识模式不正确")
    if not COLOR_RE.match(color):
        raise ValueError("品牌色必须是 #RRGGBB 格式")
    if mark_mode == "text" and not mark:
        raise ValueError("文字品牌标识不能为空")
    if mark_mode == "image" and not _icon_ext():
        raise ValueError("请先上传品牌图片")
    values = {
        "title": title,
        "subtitle": subtitle,
        "mark": mark or DEFAULTS["mark"],
        "color": color,
        "mark_mode": mark_mode,
    }
    for field, value in values.items():
        set_setting(KEYS[field], value)
    return get_branding()


def _detect_icon(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"BM"):
        return "bmp"
    return None


def save_icon(data: bytes) -> dict[str, str]:
    if not data:
        raise ValueError("图片为空")
    if len(data) > ICON_MAX_BYTES:
        raise ValueError("品牌图片不能超过 1MB")
    ext = _detect_icon(data)
    if ext is None:
        raise ValueError("仅支持 PNG/JPG/GIF/WebP/BMP 图片")
    directory = _icon_dir()
    for name in directory.glob("icon.*"):
        if name.name != f"icon.{ext}":
            name.unlink(missing_ok=True)
    target = directory / f"icon.{ext}"
    tmp = directory / ".icon.tmp"
    tmp.write_bytes(data)
    tmp.replace(target)
    set_setting(ICON_EXT_KEY, ext)
    set_setting(ICON_VERSION_KEY, str(int(time.time() * 1000)))
    set_setting(KEYS["mark_mode"], "image")
    return get_branding()


def delete_icon() -> dict[str, str]:
    for name in _icon_dir().glob("icon.*"):
        name.unlink(missing_ok=True)
    set_setting(ICON_EXT_KEY, "")
    set_setting(ICON_VERSION_KEY, "0")
    if get_setting(KEYS["mark_mode"]) == "image":
        set_setting(KEYS["mark_mode"], "text")
    return get_branding()


@router.get("/branding")
async def public_branding():
    return get_branding()


@router.get("/branding/icon")
async def public_branding_icon():
    ext = _icon_ext()
    path = _icon_dir() / f"icon.{ext}" if ext else None
    if path is None or not path.is_file():
        return Response(status_code=404)
    return Response(
        path.read_bytes(),
        media_type=ICON_TYPES[ext],
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="brand-icon.{ext}"',
        },
    )
