import base64
import json
import time
import urllib.parse
import urllib.request
import pathlib

import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"
ART = pathlib.Path("tests/artifacts")
failures = []


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


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
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)

# 拖拽遮罩提示应显示 32MB
hint = ev(
    "(() => { const dt=new DataTransfer(); dt.items.add(new File(['x'],'a.txt',{type:'text/plain'}));"
    "document.dispatchEvent(new DragEvent('dragenter',{bubbles:true,dataTransfer:dt}));"
    "return true; })()"
)
wait(0.5)
body = ev("document.body.innerText") or ""
check("每个 32MB" in body, "拖拽提示显示每个 32MB", body[-300:])
ev(
    "(() => { const dt=new DataTransfer(); dt.items.add(new File(['x'],'a.txt',{type:'text/plain'}));"
    "document.dispatchEvent(new DragEvent('dragleave',{bubbles:true,dataTransfer:dt})); return true; })()"
)
wait(0.3)

# 33MB 文本/二进制都应在客户端被 32MB 校验拦截
for name, mime in [("over.txt", "text/plain"), ("over.pdf", "application/pdf")]:
    result = ev(
        "(() => { const dt=new DataTransfer();"
        "dt.items.add(new File([new Uint8Array(33*1024*1024)]," + json.dumps(name) + ",{type:" + json.dumps(mime) + "}));"
        "document.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));"
        "return true; })()"
    )
    wait(1.2)
    body = ev("document.body.innerText") or ""
    check("32" in body and ("超过限制" in body or "exceeds" in body), f"{name} 被统一 32MB 提示拦截", body[-400:])
    shot = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})["result"]["data"]
    (ART / ("size-limit-" + name.replace(".", "-") + ".png")).write_bytes(base64.b64decode(shot))

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部 UI 32MB 上限测试通过")
