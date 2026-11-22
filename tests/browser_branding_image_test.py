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
ICON = str((ART / "dataset-image.webp").resolve())
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


def login():
    ev(
        "document.getElementById('username').value='admin';"
        "document.getElementById('password').value='admin12345';"
        "document.getElementById('login-form').requestSubmit();"
    )


cmd("Runtime.enable")
cmd("Page.enable")
cmd("DOM.enable")
cmd("Network.enable")
cmd("Network.setCacheDisabled", {"cacheDisabled": True})
cmd("Network.clearBrowserCookies")
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1.2)
login()
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/admin"})
wait(1.5)
panel_anim = ev("(()=>{const p=document.querySelector('section[data-panel=overview]'); return p?getComputedStyle(p).animationDuration:''})()")
check(panel_anim == "0.32s", "后台面板切换淡入 0.32s", str(panel_anim))
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('自定义网页')); if(b)b.click(); return !!b})()")
wait(0.8)
ev("document.getElementById('change-pw').click()")
wait(0.3)
modal_duration = ev("(()=>{const m=document.querySelector('.modal'); return m?getComputedStyle(m).animationDuration:''})()")
overlay_duration = ev("(()=>{const m=document.querySelector('.modal'); return m&&m.parentElement?getComputedStyle(m.parentElement).animationDuration:''})()")
check(modal_duration == "0.32s", "后台弹窗内容动画 0.32s", str(modal_duration))
check(overlay_duration == "0.26s", "后台弹窗遮罩动画 0.26s", str(overlay_duration))
ev("document.querySelector('.modal [data-close]').click()")
wait(0.3)

# 通过文件选择上传图片
root = cmd("DOM.getDocument", {"depth": -1})["result"]["root"]["nodeId"]
node = cmd("DOM.querySelector", {"nodeId": root, "selector": "#brand-icon-file"})["result"]["nodeId"]
cmd("DOM.setFileInputFiles", {"nodeId": node, "files": [ICON]})
wait(0.4)
ev("document.getElementById('brand-mode').value='image'; document.getElementById('brand-mode').dispatchEvent(new Event('change',{bubbles:true}));")
ev("document.getElementById('btn-upload-brand-icon').click()")
wait(1.5)
check(ev("document.getElementById('brand-mode').value") == "image", "上传后品牌模式为图片")
check("已上传" in (ev("document.getElementById('brand-icon-hint').textContent") or ""), "显示已上传品牌图片", ev("document.getElementById('brand-icon-hint').textContent"))
check("branding/icon" in (ev("document.getElementById('brand-preview-mark').style.backgroundImage") or ""), "后台预览使用品牌图片")
# 保存
ev("document.getElementById('btn-save-branding').click()")
wait(1)

# 登录页图片标识
cmd("Page.navigate", {"url": BASE + "/logout"})
wait(1)
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1.5)
check("branding/icon" in (ev("document.querySelector('.logo-mark').style.backgroundImage") or ""), "登录页使用图片品牌标识")
check(ev("document.querySelector('.logo-mark').textContent.trim()") == "", "图片模式不显示文字标识")
shot = cmd("Page.captureScreenshot", {"format": "png", "fromSurface": True})["result"]["data"]
(ART / "branding-image-login.png").write_bytes(base64.b64decode(shot))
check(ev("document.querySelector('.logo-mark').style.display") != "none", "图片标识可见")

# 关闭品牌标识
login()
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/admin"})
wait(1.5)
panel_anim = ev("(()=>{const p=document.querySelector('section[data-panel=overview]'); return p?getComputedStyle(p).animationDuration:''})()")
check(panel_anim == "0.32s", "后台面板切换淡入 0.32s", str(panel_anim))
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('自定义网页')); if(b)b.click(); return !!b})()")
wait(0.8)
ev("document.getElementById('brand-mode').value='none'; document.getElementById('brand-mode').dispatchEvent(new Event('change',{bubbles:true}));")
ev("document.getElementById('btn-save-branding').click()")
wait(0.8)
cmd("Page.navigate", {"url": BASE + "/logout"})
wait(1)
cmd("Page.navigate", {"url": BASE + "/login"})
wait(1.5)
check(ev("getComputedStyle(document.querySelector('.logo-mark')).display") == "none", "关闭品牌标识后登录页隐藏图标")
check(ev("document.querySelector('[data-brand-name]').textContent") == "GuGuAI", "关闭标识不影响主标题")

# 恢复文字模式，避免测试库影响后续
login()
wait(2.5)
cmd("Page.navigate", {"url": BASE + "/admin"})
wait(1.5)
panel_anim = ev("(()=>{const p=document.querySelector('section[data-panel=overview]'); return p?getComputedStyle(p).animationDuration:''})()")
check(panel_anim == "0.32s", "后台面板切换淡入 0.32s", str(panel_anim))
ev("(()=>{const b=[...document.querySelectorAll('.nav-item')].find(x=>(x.innerText||'').includes('自定义网页')); if(b)b.click(); return !!b})()")
wait(0.8)
ev("document.getElementById('brand-mode').value='text'; document.getElementById('brand-mode').dispatchEvent(new Event('change',{bubbles:true}));")
ev("document.getElementById('brand-mark').value='G'; document.getElementById('btn-save-branding').click()")
wait(0.5)

ws.close()
print()
if failures:
    print("失败：", failures)
    raise SystemExit(1)
print("全部品牌图片/关闭标识测试通过")
