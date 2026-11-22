import base64
import json
import time
import urllib.parse
import urllib.request
import pathlib

import httpx
import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"
ART = pathlib.Path("tests/artifacts")
IMG = ART / "dataset-image.webp"
failures = []


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


api = httpx.Client(base_url=BASE, timeout=60)
api.post("/api/auth/login", json={"username": "admin", "password": "admin12345"})
with IMG.open("rb") as f:
    up = api.post("/api/files", files={"file": (IMG.name, f, "image/webp")})
check(up.status_code == 200, "测试数据集图片上传", up.text[:200])
info = up.json()
now = int(time.time() * 1000)
session = {
    "id": "image-preview-test",
    "title": "图片预览测试",
    "createdAt": now,
    "updatedAt": now,
    "messages": [
        {
            "id": "image-u",
            "role": "user",
            "content": "测试图片",
            "files": [
                {
                    "id": "image-local",
                    "serverId": info["id"],
                    "name": info["name"],
                    "size": info["size"],
                    "isImage": True,
                    "mimeType": info["mime"],
                }
            ],
            "createdAt": now,
        }
    ],
}
check(api.put("/api/sync/sessions/image-preview-test", json=session).status_code == 200, "创建图片会话")

target = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            f"{CDP}/json/new?{urllib.parse.quote(BASE + '/login', safe='')}", method="PUT"
        )
    )
)
ws = wsclient.connect(target["webSocketDebuggerUrl"], max_size=None)
mid = 0


def cmd(method, params=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv(timeout=30))
        if msg.get("id") == mid:
            return msg


def ev(expr):
    res = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return res.get("result", {}).get("result", {}).get("value")


def wait(seconds):
    time.sleep(seconds)


cmd("Runtime.enable")
cmd("Page.enable")
cmd("Network.enable")
cmd("Network.clearBrowserCookies")
wait(1)
ev(
    "document.getElementById('username').value='admin';"
    "document.getElementById('password').value='admin12345';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)

clicked = ev(
    "(() => { const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('图片预览测试'));"
    "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true; })()"
)
check(clicked, "打开图片会话")
wait(2)

image_title = info["name"]
opened = ev(
    "(() => { const b=[...document.querySelectorAll('button')].find(x=>x.title===" + json.dumps(image_title) + ");"
    "if(!b) return false; b.click(); return true; })()"
)
check(opened, "点击图片缩略图", image_title)
wait(1.2)

check(ev("document.querySelectorAll('iframe').length") == 0, "图片预览不使用 iframe")
check(ev("document.querySelectorAll('img[src^=\"data:image/\"]').length") >= 1, "图片预览使用 data URL")
check(ev("(document.body.innerText||'').includes(" + json.dumps(image_title) + ")"), "预览标题显示文件名")
check(ev("!!document.querySelector('[aria-label=\"关闭\"]') || !!document.querySelector('[class*=close]')"), "预览有关闭按钮")

data = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})["result"]["data"]
path = ART / "image-preview.png"
path.write_bytes(base64.b64decode(data))
print("screenshot", path)
ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部图片预览测试通过")
