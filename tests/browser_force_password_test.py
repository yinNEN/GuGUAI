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
ART.mkdir(parents=True, exist_ok=True)
failures = []
FORCE_USER = f"forceuser{int(time.time())}"


def check(ok, name, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failures.append(name)


# 准备被标记用户
api = httpx.Client(base_url=BASE, timeout=30)
api.post("/api/auth/login", json={"username": "admin", "password": "admin12345"})
r = api.post(
    "/api/admin/users",
    json={"username": FORCE_USER, "password": "oldpass123", "role": "user", "must_change_password": True},
)
check(r.status_code in (200, 409), "创建/复用被标记用户", r.text)

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


def shot(name):
    data = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})["result"]["data"]
    path = ART / name
    path.write_bytes(base64.b64decode(data))
    print("screenshot", path)


cmd("Runtime.enable")
cmd("Page.enable")
cmd("Network.enable")
cmd("Network.clearBrowserCookies")
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1.5)

# 登录被标记用户
ev(
    "document.getElementById('username').value=" + json.dumps(FORCE_USER) + ";"
    "document.getElementById('password').value='oldpass123';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(2)
check(ev("document.getElementById('force-overlay').classList.contains('open')"), "登录后被强制弹出改密窗口")
check(ev("location.pathname") == "/login", "改密前仍停留在登录页")
shot("force-password-modal.png")

blocked = ev("fetch('/api/admin/users').then(r=>r.status).catch(()=>'ERR')")
check(blocked == 403, "改密前管理接口被拒绝", str(blocked))
blocked_chat = ev("fetch('/api/sync/sessions').then(r=>r.status).catch(()=>'ERR')")
check(blocked_chat == 403, "改密前聊天同步接口被拒绝", str(blocked_chat))

# 修改密码并重新登录
ev("document.getElementById('force-new').value='newpass456';"
   "document.getElementById('force-confirm').value='newpass456';"
   "document.getElementById('force-save').click();")
wait(2.5)
check(ev("location.pathname") == "/login", "改密后回到登录页", ev("location.pathname"))
check("changed=1" in (ev("location.search") or ""), "改密后提示重新登录")
check(ev("document.getElementById('force-overlay').classList.contains('open')") is False, "改密后弹窗关闭")

# 旧密码失败，新密码成功
ev(
    "document.getElementById('username').value=" + json.dumps(FORCE_USER) + ";"
    "document.getElementById('password').value='oldpass123';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(1.5)
check("错误" in (ev("document.getElementById('error').textContent") or ""), "旧密码已失效", ev("document.getElementById('error').textContent"))
ev("document.getElementById('password').value='newpass456'; document.getElementById('login-form').requestSubmit();")
wait(2.5)
check(ev("location.pathname") == "/", "新密码登录成功", ev("location.pathname"))

# 普通用户不能访问管理面板/接口
cmd("Page.navigate", {"url": BASE + "/admin"})
wait(1.5)
check(ev("location.pathname") == "/ui/history", "普通用户访问后台被重定向", ev("location.pathname"))
r = ev("fetch('/api/admin/users').then(r=>r.status).catch(()=>'ERR')")
check(r == 403, "普通用户管理接口 403", str(r))
r = ev("fetch('/api/admin/storage').then(r=>r.status).catch(()=>'ERR')")
check(r == 403, "普通用户存储管理接口 403", str(r))

# 管理员登录并检查管理面板
cmd("Page.navigate", {"url": BASE + "/logout"})
wait(1.5)
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1)
ev(
    "document.getElementById('username').value='admin';"
    "document.getElementById('password').value='admin12345';"
    "document.getElementById('login-form').requestSubmit();"
)
wait(2.5)
check(ev("location.pathname") == "/admin", "管理员进入后台", ev("location.pathname"))
check("GuGuAI 管理后台" in (ev("document.body.innerText") or ""), "后台品牌显示正常")
check(ev("document.querySelectorAll('#users-body tr').length") >= 2, "后台用户列表已加载")
shot("admin-panel.png")

# 管理后台修改用户时“下次登录必须修改密码”默认不勾选（新建）
ev("document.getElementById('btn-new-user').click()")
wait(0.8)
check(ev("document.getElementById('f-must').checked") is False, "新建用户默认不强制改密")
# 编辑普通用户时默认不勾选强制改密
ev("document.querySelector('[data-close]').click()")
wait(0.3)
ev(
    "(() => { const row=[...document.querySelectorAll('#users-body tr')]"
    ".find(r=>r.innerText.includes(" + json.dumps(FORCE_USER) + "));"
    "if(!row) return false; row.querySelector('[data-edit]').click(); return true; })()"
)
wait(0.8)
check(ev("document.getElementById('f-must').checked") is False, "编辑普通用户默认不勾选强制改密")

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部强制改密/后台权限测试通过")
