import json
import time
import urllib.parse
import urllib.request

import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"

now = int(time.time() * 1000)
session = {
    "id": "xss-ui-test-0001",
    "title": "XSS沙箱测试",
    "createdAt": now,
    "updatedAt": now,
    "messages": [
        {"id": "x-u", "role": "user", "content": "测试", "files": [], "createdAt": now},
        {
            "id": "x-a",
            "role": "assistant",
            "createdAt": now + 1,
            "model": "deepseek-flash",
            "prefix": False,
            "status": "done",
            "blocks": [
                {
                    "type": "text",
                    "text": (
                        "```html\n<h1 id=\"xss-inner\">inner</h1>"
                        "<script>try{parent.document.title='PWNED';"
                        "parent.document.body.setAttribute('data-xss','1')}catch(e){"
                        "document.body.setAttribute('data-blocked','1')}</script>\n```"
                    ),
                }
            ],
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
print(ev(
    "fetch('/api/sync/sessions/xss-ui-test-0001',{method:'PUT',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify(" + json.dumps(session) + ")})"
    ".then(r=>r.text())"
))
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)
print("clicked session", ev(
    "(() => { const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('XSS沙箱测试'));"
    "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true; })()"
))
wait(2)
print("open preview", ev(
    "(() => { const b=Array.from(document.querySelectorAll('button')).find(x=>x.innerText.trim()==='预览');"
    "if(!b) return false; b.click(); return true; })()"
))
wait(1.5)
print("iframes", ev("document.querySelectorAll('iframe').length"))
print("parent title", ev("document.title"))
print("parent xss attr", ev("document.body.getAttribute('data-xss')"))
print("body contains PWNED", ev("(document.body.innerText||'').includes('PWNED')"))
ws.close()
