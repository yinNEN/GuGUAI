import base64
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
import pathlib

import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"
ART = pathlib.Path("tests/artifacts")
ART.mkdir(parents=True, exist_ok=True)

# 从测试数据集中取最后一个 SVG 代码块
z = zipfile.ZipFile("测试数据集.zip")
data = json.loads(z.read("chat_sessions/4ac0f704-95fd-48c1-860b-1a00362df314.json"))
blocks = []


def walk(obj):
    if isinstance(obj, dict):
        for value in obj.values():
            walk(value)
    elif isinstance(obj, list):
        for value in obj:
            walk(value)
    elif isinstance(obj, str):
        blocks.extend(m.group(1) for m in re.finditer(r"```svg\s*\n(.*?)```", obj, re.S))


walk(data)
svg = blocks[-1]
print("svg bytes", len(svg.encode("utf-8")))

now = int(time.time() * 1000)
session = {
    "id": "svg-ui-test-0001",
    "title": "SVG预览测试",
    "createdAt": now,
    "updatedAt": now,
    "messages": [
        {
            "id": "svg-u1",
            "role": "user",
            "content": "请生成一个 SVG",
            "files": [],
            "createdAt": now,
        },
        {
            "id": "svg-a1",
            "role": "assistant",
            "createdAt": now + 1,
            "model": "deepseek-flash",
            "prefix": False,
            "status": "done",
            "blocks": [{"type": "text", "text": "```svg\n" + svg + "\n```"}],
        },
    ],
}

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
wait(1)
ev(
    "document.getElementById('username').value='admin';"
    "document.getElementById('password').value='admin12345';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(2.5)

created = ev(
    "fetch('/api/sync/sessions/svg-ui-test-0001',{method:'PUT',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify(" + json.dumps(session) + ")})"
    ".then(r=>r.text())"
)
print("session created", created)

cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)

# 点击侧边栏的测试会话
found = ev(
    "(() => { const nodes=[...document.querySelectorAll('*')];"
    "const node=nodes.find(e=>e.children.length===0 && (e.textContent||'').includes('SVG预览测试'));"
    "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true; })()"
)
print("session clicked", found)
wait(2)

buttons = ev("Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean).slice(0,40)")
print("buttons", buttons)
clicked = ev(
    "(() => { const b=Array.from(document.querySelectorAll('button')).find(x=>x.innerText.trim()==='预览');"
    "if(!b) return false; b.click(); return true; })()"
)
print("preview clicked", clicked)
wait(1.5)
print("iframes", ev("document.querySelectorAll('iframe').length"))
print("iframe srcdoc length", ev("(document.querySelector('iframe')||{}).srcdoc?.length || 0"))
print("iframe rect", ev("(()=>{const f=document.querySelector('iframe'); if(!f)return null; const r=f.getBoundingClientRect(); return {w:r.width,h:r.height};})()"))
res = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})
path = ART / "svg-preview.png"
path.write_bytes(base64.b64decode(res["result"]["data"]))
print("screenshot", path)

ws.close()
