from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .common import current_user
from .config import settings

router = APIRouter()


def _page(name: str) -> FileResponse:
    return FileResponse(
        settings.pages_dir / name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(
        settings.web_dir / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/login", include_in_schema=False)
async def login_page(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return _page("login.html")


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request):
    if (current_user(request) or {}).get("role") != "admin":
        return RedirectResponse("/ui/history", status_code=302)
    return _page("admin.html")


@router.get("/ui/history", include_in_schema=False)
async def history_page() -> FileResponse:
    return _page("history.html")


@router.get("/ui/account", include_in_schema=False)
async def account_page() -> FileResponse:
    return _page("account.html")


@router.get("/logout", include_in_schema=False)
async def logout(request: Request):
    from .security import token_hash
    from .store import delete_session

    token = request.cookies.get(settings.cookie_name)
    if token:
        delete_session(token_hash(token))
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(settings.cookie_name, path="/")
    return resp


@router.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "deepseek-chat-server"})


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)
