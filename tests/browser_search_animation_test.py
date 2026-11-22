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

# 如果上一次测试留下了收起状态，先展开
if ev("!!document.querySelector('button[aria-label=\"打开边栏\"]')"):
    ev("document.querySelector('button[aria-label=\"打开边栏\"]').click()")
    wait(0.5)

# 等待同步会话出现
for _ in range(20):
    if ev("(document.body.innerText||'').includes('锐评项目代码')"):
        break
    wait(0.5)

check(ev("!!document.querySelector('button[aria-label=\"搜索聊天\"]')"), "侧边栏有搜索按钮")

# 先打开三个会话，把详情写入本地 IndexedDB，搜索只读本地缓存
for title in ["锐评项目代码", "一般性题目代码锐评", "无关会话"]:
    ev("(()=>{const node=[...document.querySelectorAll('*')].find(e=>e.children.length===0&&(e.textContent||'').includes(" + json.dumps(title) + "));"
       "if(!node) return false; (node.closest('button')||node.parentElement).click(); return true})()")
    wait(0.8)
check(ev("!!document.querySelector('[data-message-id=\"search-c-a\"]')"), "会话详情已加载")
page_anim = ev("(()=>{const e=document.querySelector('[class*=view]'); return e?getComputedStyle(e).animationName:''})()")
check("fade" in (page_anim or ""), "页面内容有淡入动画", page_anim)
check(ev("(()=>{const e=document.querySelector('[class*=view]'); return e?getComputedStyle(e).animationDuration:''})()") == "0.32s", "页面淡入时长 0.32s")

ev("document.querySelector('button[aria-label=\"搜索聊天\"]').click()")
wait(0.5)
check(ev("!!document.querySelector('[role=dialog]')"), "搜索面板打开")
panel_anim = ev("(()=>{const p=document.querySelector('[role=dialog]'); return p?getComputedStyle(p).animationName:''})()")
check("search" in (panel_anim or ""), "搜索面板有过渡动画", panel_anim)
check(ev("(()=>{const p=document.querySelector('[role=dialog]'); return p?getComputedStyle(p).animationDuration:''})()") == "0.32s", "搜索面板时长 0.32s")
check(ev("(()=>{const p=document.querySelector('[role=dialog]'); return p&&p.parentElement?getComputedStyle(p.parentElement).animationDuration:''})()") == "0.26s", "搜索遮罩时长 0.26s")
check(ev("document.activeElement && document.activeElement.tagName === 'INPUT'"), "搜索输入框自动聚焦")

# 输入关键词
ev(
    "(()=>{const i=document.querySelector('[role=dialog] input');"
    "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
    "s.call(i,'锐评'); i.dispatchEvent(new Event('input',{bubbles:true})); return true})()"
)
wait(1)
body = ev("document.body.innerText") or ""
check("锐评项目代码" in body and "一般性题目代码锐评" in body, "搜索到相关会话")
check(ev("document.querySelectorAll('[role=dialog] mark').length") >= 2, "搜索结果高亮关键词")
check(ev("(()=>{const b=document.querySelector('[role=dialog] button[class*=result]'); return b?getComputedStyle(b).animationDuration:''})()") == "0.24s", "搜索结果行时长 0.24s")
check("无关会话" not in (ev("(document.querySelector('[role=dialog]')||{}).innerText || ''")), "不返回不相关会话")
shot("chat-search.png")

# 点击第一条结果
clicked = ev(
    "(()=>{const b=[...document.querySelectorAll('[role=dialog] button')].find(x=>(x.innerText||'').includes('锐评项目代码'));"
    "if(!b) return false; b.click(); return true})()"
)
check(clicked, "点击搜索结果")
wait(1.5)
check(ev("!document.querySelector('[role=dialog]')"), "点击结果后搜索面板关闭")
check(ev("(document.body.innerText||'').includes('锐评项目代码')"), "打开对应会话")
check(ev("!!document.querySelector('[data-message-id=\"search-a-a\"]')"), "定位到匹配消息")
shot("chat-search-result.png")

# 确保搜索面板关闭
ev("(()=>{const b=document.querySelector('[role=dialog] button[aria-label=\"关闭\"]'); if(b)b.click(); return true})()")
wait(0.3)

# 侧边栏收起/展开动画
buttons = ev("Array.from(document.querySelectorAll('button')).map(b=>b.getAttribute('aria-label')||b.title||'').filter(Boolean)")
print("buttons", buttons[:30])
collapse = ev("(()=>{const b=document.querySelector('button[aria-label=\"收起边栏\"]'); if(!b)return false; b.click(); return true})()")
check(collapse, "找到收起边栏按钮")
wait(0.05)
width_start = ev("(()=>{const e=document.querySelector('[class*=siderSlot]'); return e?e.getBoundingClientRect().width:-1})()")
wait(0.45)
width_end = ev("(()=>{const e=document.querySelector('[class*=siderSlot]'); return e?e.getBoundingClientRect().width:-1})()")
check(width_start >= 0 and width_end == 0, "侧边栏宽度过渡到 0", f"{width_start}->{width_end}")
check(ev("(()=>{const e=document.querySelector('[class*=siderSlot]'); return e?getComputedStyle(e).transitionDuration:''})()").startswith("0.28s"), "侧边栏动画时长保持 0.28s")
check(ev("!!document.querySelector('button[aria-label=\"展开边栏\"]')") or ev("!!document.querySelector('button[aria-label=\"搜索聊天\"]')"), "收起后显示折叠操作区")
# 展开
expanded = ev("(()=>{const b=document.querySelector('button[aria-label=\"打开边栏\"]'); if(!b)return false; b.click(); return true})()")
check(expanded, "找到展开边栏按钮")
wait(0.45)
check(ev("(()=>{const e=document.querySelector('[class*=siderSlot]'); return e?Math.round(e.getBoundingClientRect().width):-1})()") == 261, "侧边栏展开恢复宽度")

# 设置弹窗动画
ev("(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='设置'); if(b)b.click(); return !!b})()")
wait(0.5)
check(ev("!!document.querySelector('[role=dialog]')"), "设置弹窗打开")
modal_anim = ev("(()=>{const d=document.querySelector('[role=dialog]'); return d?getComputedStyle(d).animationName:''})()")
check("modal" in (modal_anim or ""), "弹窗有过渡动画", modal_anim)
check(ev("(()=>{const d=document.querySelector('[role=dialog]'); return d?getComputedStyle(d).animationDuration:''})()") == "0.32s", "弹窗内容时长 0.32s")
shot("settings-animation.png")

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部搜索/动画测试通过")
