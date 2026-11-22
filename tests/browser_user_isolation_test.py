import base64
import json
import os
import time
import urllib.parse
import urllib.request
import pathlib

import websockets.sync.client as wsclient

BASE = "http://127.0.0.1:18080"
CDP = "http://127.0.0.1:9222"
ART = pathlib.Path("tests/artifacts")
ALICE = os.environ.get("ISO_ALICE", "alice1795259591")
BOB = os.environ.get("ISO_BOB", "bob1795259591")
ALICE_ID = int(os.environ.get("ISO_ALICE_ID", "3"))
BOB_ID = int(os.environ.get("ISO_BOB_ID", "4"))
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


def login(username, password):
    ev(
        "document.getElementById('username').value=" + json.dumps(username) + ";"
        "document.getElementById('password').value=" + json.dumps(password) + ";"
        "document.getElementById('login-form').requestSubmit();"
    )


cmd("Runtime.enable")
cmd("Page.enable")
cmd("Network.enable")
cmd("Network.clearBrowserCookies")
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1.2)

# Alice 登录并打开会话，使详情写入 Alice 的 IndexedDB
login(ALICE, "secret123")
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("(document.body.innerText||'').includes('ALICE_ONLY_SECRET')"):
        break
    wait(0.5)
check("ALICE_ONLY_SECRET" in (ev("document.body.innerText") or ""), "Alice 看到自己的会话")
ev("(()=>{const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('ALICE_ONLY_SECRET')); if(!n)return false; (n.closest('button')||n.parentElement).click(); return true})()")
wait(1.2)
check("Alice secret message" in (ev("document.body.innerText") or ""), "Alice 会话详情已加载")
alice_db = ev("new Promise(r=>indexedDB.databases().then(d=>r(d.map(x=>x.name))))")
check(f"chat-demo-u{ALICE_ID}" in (alice_db or []), "Alice 使用独立 IndexedDB", str(alice_db))

# 退出 Alice，登录 Bob
cmd("Page.navigate", {"url": BASE + "/logout"})
wait(1.2)
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1)
login(BOB, "secret123")
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
for _ in range(20):
    if ev("!!document.querySelector('textarea')"):
        break
    wait(0.5)
body = ev("document.body.innerText") or ""
check("ALICE_ONLY_SECRET" not in body, "Bob 看不到 Alice 的会话")
check("Alice secret message" not in body, "Bob 看不到 Alice 的消息")
dbs = ev("new Promise(r=>indexedDB.databases().then(d=>r(d.map(x=>x.name))))")
check(f"chat-demo-u{BOB_ID}" in (dbs or []), "Bob 使用独立 IndexedDB", str(dbs))
bob_count = ev(
    "new Promise(r=>{const q=indexedDB.open('chat-demo-u" + str(BOB_ID) + "');"
    "q.onsuccess=()=>{const db=q.result; if(!db.objectStoreNames.contains('sessionSummaries')){db.close();r(0);return;}"
    "const t=db.transaction('sessionSummaries','readonly'); const c=t.objectStore('sessionSummaries').count();"
    "c.onsuccess=()=>{r(c.result);db.close()}; c.onerror=()=>{r(-1);db.close()}}; q.onerror=()=>r(-1)})"
)
check(bob_count == 0, "Bob 的本地会话缓存为空", str(bob_count))
check(ev("!document.querySelector('[data-message-id]')"), "Bob 界面没有渲染 Alice 消息")

# 切回 Alice，仍能看到自己的本地/服务端会话
cmd("Page.navigate", {"url": BASE + "/logout"})
wait(1)
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1)
login(ALICE, "secret123")
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/"})
wait(3)
check("ALICE_ONLY_SECRET" in (ev("document.body.innerText") or ""), "Alice 重新登录仍看到自己的会话")

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部用户数据隔离测试通过")
