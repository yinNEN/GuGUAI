import json
import time
import urllib.parse
import urllib.request
import pathlib

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
events = []


def cmd(method, params=None):
    global mid
    mid += 1
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv(timeout=30))
        if msg.get("id") == mid:
            return msg
        if "method" in msg:
            events.append(msg)


def ev(expr):
    res = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return res.get("result", {}).get("result", {}).get("value")


def shot(name):
    res = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})
    data = res["result"]["data"]
    import base64

    path = ART / name
    path.write_bytes(base64.b64decode(data))
    print("screenshot", path)


def wait(seconds=1):
    time.sleep(seconds)


cmd("Runtime.enable")
cmd("Page.enable")
cmd("DOM.enable")
wait(1.5)

print("path", ev("location.pathname"))
ev(
    "document.getElementById('username').value='admin';"
    "document.getElementById('password').value='admin12345';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(3)
print("after login", ev("location.pathname"))

cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(30):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)
print("chat ready", ev("!!document.querySelector('textarea')"))

# 先在输入框里输入需求
ev(
    "(() => { const el=document.querySelector('textarea');"
    "const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
    "setter.call(el,'继续锐评'); el.dispatchEvent(new Event('input',{bubbles:true})); return el.value; })()"
)

long_code = "#define INT_MAX 2147483647\n" + "\n".join(
    f"int value_{i} = {i}; // 这是一段用于测试粘贴转换的长代码" for i in range(120)
)
print("long code chars", len(long_code))
# 触发 paste 事件
result = ev(
    "(() => { const el=document.querySelector('textarea');"
    "const dt=new DataTransfer(); dt.setData('text/plain', " + json.dumps(long_code) + ");"
    "const event=new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt});"
    "const ok=el.dispatchEvent(event);"
    "return {ok, value: el.value, prevented: event.defaultPrevented}; })()"
)
print("paste result", result)
wait(1.2)
print("textarea after paste:", repr(ev("document.querySelector('textarea').value")))
print("body text excerpt:", (ev("document.body.innerText") or "")[:500].replace("\n", " | "))
print("attachment cards:", ev("Array.from(document.querySelectorAll('div')).filter(d=>d.innerText&&d.innerText.includes('.txt')&&d.innerText.includes('TXT')).length"))
shot("paste-behavior.png")

# 手动输入长文本：不应转附件
ev(
    "(() => { const el=document.querySelector('textarea');"
    "const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
    "const text='A'.repeat(2500);"
    "setter.call(el,text); el.dispatchEvent(new Event('input',{bubbles:true})); return el.value.length; })()"
)
wait(0.5)
print("manual long text length:", ev("document.querySelector('textarea').value.length"))
print("body still has attachment:", ev("document.body.innerText.includes('.txt')"))

ws.close()
