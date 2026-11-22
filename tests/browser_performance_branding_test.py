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


def shot(name):
    data = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})["result"]["data"]
    path = ART / name
    path.write_bytes(base64.b64decode(data))
    print("screenshot", path)


cmd("Runtime.enable")
cmd("Page.enable")
cmd("Network.enable")
cmd("Network.clearBrowserCookies")
wait(1.5)

# 登录页品牌
check(ev("document.querySelector('[data-brand-name]') && document.querySelector('[data-brand-name]').textContent") == "测试品牌", "登录页主标题使用后台品牌", ev("document.querySelector('[data-brand-name]')?.textContent"))
check(ev("document.querySelector('[data-brand-mark]') && document.querySelector('[data-brand-mark]').textContent") == "T", "登录页品牌标识")
check(ev("getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()") == "#12ab34", "登录页品牌色")
shot("branding-login.png")

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
check("测试品牌" in (ev("document.body.innerText") or ""), "聊天侧边栏使用后台品牌")
check(ev("document.title") == "测试品牌", "聊天页标题使用后台品牌", ev("document.title"))

# 打开大聊天，立即检查加载提示
opened = ev(
    "(()=>{const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('大型聊天性能测试'));"
    "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true})()"
)
check(opened, "打开大型聊天")
wait(0.02)
check("正在加载对话" in (ev("document.body.innerText") or ""), "切换会话立即显示加载提示")
wait(0.35)
body = ev("document.body.innerText") or ""
check("大型聊天性能测试" in body, "大型聊天已显示")
count = ev("document.querySelectorAll('[data-message-id]').length")
check(0 < count <= 24, "大聊天首屏只渲染部分消息", str(count))
shot("large-chat-progressive.png")

# 切到小聊天再切回，仍应出现加载提示
ev("(()=>{const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('小聊天'));"
   "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true})()")
wait(0.05)
check("正在加载对话" in (ev("document.body.innerText") or ""), "再次切换会话仍显示加载提示")
wait(0.4)
ev("(()=>{const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes('大型聊天性能测试'));"
   "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true})()")
wait(0.05)
check("正在加载对话" in (ev("document.body.innerText") or ""), "返回大聊天仍显示加载提示")
wait(0.5)

# 向上滚动加载更多
before = ev("document.querySelectorAll('[data-message-id]').length")
scrolled = ev(
    "(()=>{const nodes=[...document.querySelectorAll('[data-message-id]')]; if(!nodes.length) return false;"
    "let e=nodes[0].parentElement; while(e && !(e.scrollHeight>e.clientHeight && getComputedStyle(e).overflowY!=='visible')) e=e.parentElement;"
    "if(!e) return false; e.scrollTop=0; e.dispatchEvent(new Event('scroll',{bubbles:true})); return true})()"
)
check(scrolled, "找到消息滚动容器")
wait(0.5)
after = ev("document.querySelectorAll('[data-message-id]').length")
check(after > before, "滚动到顶部后加载更早消息", f"{before}->{after}")
check(ev("(document.body.innerText||'').includes('加载更早的消息')") is False or after >= 40, "已展示更早消息区间")

# 搜索旧消息并展开渲染窗口
ev('document.querySelector(\'button[aria-label="搜索聊天"]\').click()')
wait(0.4)
ev(
    "(()=>{const i=document.querySelector('[role=dialog] input');"
    "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
    "s.call(i,'第 1 个回答'); i.dispatchEvent(new Event('input',{bubbles:true})); return true})()"
)
wait(0.8)
clicked = ev(
    "(()=>{const b=[...document.querySelectorAll('[role=dialog] button')].find(x=>(x.innerText||'').includes('大型聊天性能测试'));"
    "if(!b) return false; b.click(); return true})()"
)
check(clicked, "搜索到并点击旧消息")
wait(1)
check(ev("!!document.querySelector('[data-message-id=big-a1]')"), "搜索跳转会扩充消息渲染窗口")

# 管理后台品牌设置
cmd("Page.navigate", {"url": BASE + "/admin"})
wait(1.5)
check(ev("location.pathname") == "/admin", "管理员后台可访问")
nav = ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('自定义网页')); if(!b)return false; b.click(); return true})()")
check(nav, "打开自定义网页设置")
wait(0.8)
check(ev("document.getElementById('brand-title').value") == "测试品牌", "后台品牌表单已加载")
check(ev("document.getElementById('brand-mark').value") == "T", "后台品牌标识已加载")
shot("branding-admin.png")

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部性能/品牌/加载测试通过")
