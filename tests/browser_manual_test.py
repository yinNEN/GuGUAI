import json
import time
import urllib.parse
import urllib.request
import pathlib
import base64

import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"
ART = pathlib.Path("tests/artifacts")
ART.mkdir(parents=True, exist_ok=True)

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

# 手动输入 2500 字，发送时应转为 txt 附件
length = ev(
    "(() => { const el=document.querySelector('textarea');"
    "const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
    "setter.call(el,'B'.repeat(2500)); el.dispatchEvent(new Event('input',{bubbles:true})); return el.value.length; })()"
)
wait(0.8)
print("manual length", length)
print("has txt card before send", ev("document.body.innerText.includes('TXT ') || document.body.innerText.includes('.txt')"))

# 按 Enter 发送：应转为 txt 附件并保留提示
ev(
    "(() => { const el=document.querySelector('textarea');"
    "el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true})); return true; })()"
)
wait(2)
print("textarea after convert", repr(ev("document.querySelector('textarea').value")))
print("attachment after convert", ev("document.body.innerText.includes('TXT ') || document.body.innerText.includes('.txt')"))
print("long bubble exists", ev("(() => { const els=[...document.querySelectorAll('div')];"
    "return els.some(e=>e.innerText && e.innerText.startsWith('BBBBBBBBBB')); })()"))
res = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})
path = ART / "manual-long-text-converted.png"
path.write_bytes(base64.b64decode(res["result"]["data"]))
print("screenshot", path)

# 再点发送，附件按“阅读附件”方式交给模型
ev("(() => { const b=Array.from(document.querySelectorAll('button')).find(x=>(x.getAttribute('aria-label')||'')==='发送'); if(b) b.click(); return !!b; })()")
wait(3)
body = ev("document.body.innerText") or ""
print("has mock reply", "[mock]" in body or "mock" in body)
print("prompt bubble", "请阅读附件中的内容" in body)
ws.close()
